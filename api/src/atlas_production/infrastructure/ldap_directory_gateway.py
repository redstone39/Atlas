from __future__ import annotations

import re
import ssl
from collections.abc import Callable, Mapping
from uuid import UUID

from ldap3 import Connection, NONE, SAFE_SYNC, SUBTREE, Server, Tls
from ldap3.core.exceptions import LDAPException, LDAPInvalidFilterError
from ldap3.operation.search import parse_filter
from ldap3.utils.conv import escape_bytes, escape_filter_chars
from ldap3.utils.dn import parse_dn

from atlas_production.modules.identity_access.directory_records import (
    DirectoryConnectionRecord,
    DirectoryGatewayError,
    DirectoryPrincipal,
)


_ATTRIBUTE_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9-]*|[0-9]+(?:\.[0-9]+)+)$")


def validate_directory_filter(value: str) -> None:
    try:
        parse_filter(value, None, False, False, None, False)
    except (LDAPInvalidFilterError, TypeError, ValueError):
        raise ValueError("directory user filter is invalid") from None


class LdapDirectoryGateway:
    def __init__(
        self,
        *,
        custom_ca_resolver: Callable[[str], str | None] | None = None,
        connection_factory: Callable[..., Any] = Connection,
    ) -> None:
        self._custom_ca_resolver = custom_ca_resolver or (lambda _connection_id: None)
        self._connection_factory = connection_factory

    def test_connection(
        self, connection: DirectoryConnectionRecord, bind_password: str
    ) -> None:
        client = self._service_client(connection, bind_password)
        try:
            self._search(
                client,
                connection.user_base_dn,
                connection.user_object_filter,
                (connection.stable_id_attribute,),
                size_limit=1,
            )
        finally:
            self._unbind(client)

    def search_users(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        query: str,
        limit: int,
    ) -> tuple[DirectoryPrincipal, ...]:
        self._validate_connection(connection)
        escaped = escape_filter_chars(query.strip())
        search_filter = (
            f"(&{connection.user_object_filter}"
            f"(|({connection.login_attribute}=*{escaped}*)"
            f"({connection.display_name_attribute}=*{escaped}*)"
            f"({connection.email_attribute}=*{escaped}*)))"
        )
        client = self._service_client(connection, bind_password)
        try:
            response = self._search(
                client,
                connection.user_base_dn,
                search_filter,
                self._attributes(connection),
                size_limit=limit,
            )
            principals = [self._principal(connection, entry) for entry in response]
            return tuple(principals[:limit])
        finally:
            self._unbind(client)

    def fetch_user(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        external_subject: str,
    ) -> DirectoryPrincipal | None:
        principal, _dn = self._fetch_with_dn(connection, bind_password, external_subject)
        return principal

    def authenticate(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        external_subject: str,
        password: str,
    ) -> DirectoryPrincipal:
        principal, dn = self._fetch_with_dn(connection, bind_password, external_subject)
        if principal is None or dn is None or principal.directory_enabled is False:
            raise DirectoryGatewayError("invalid_credentials")
        user_client = self._client(connection, user=dn, password=password)
        try:
            self._open_bind(connection, user_client, invalid_is_credentials=True)
        finally:
            self._unbind(user_client)
        return principal

    def _fetch_with_dn(
        self,
        connection: DirectoryConnectionRecord,
        bind_password: str,
        external_subject: str,
    ) -> tuple[DirectoryPrincipal | None, str | None]:
        self._validate_connection(connection)
        escaped_subject = self._subject_filter_value(connection, external_subject)
        search_filter = (
            f"(&{connection.user_object_filter}"
            f"({connection.stable_id_attribute}={escaped_subject}))"
        )
        client = self._service_client(connection, bind_password)
        try:
            response = self._search(
                client,
                connection.user_base_dn,
                search_filter,
                self._attributes(connection),
                size_limit=2,
            )
        finally:
            self._unbind(client)
        if not response:
            return None, None
        if len(response) != 1:
            raise DirectoryGatewayError("directory_entry_invalid")
        entry = response[0]
        dn = entry.get("dn")
        if not isinstance(dn, str) or not dn:
            raise DirectoryGatewayError("directory_entry_invalid")
        return self._principal(connection, entry), dn

    def _service_client(
        self, connection: DirectoryConnectionRecord, bind_password: str
    ) -> Any:
        self._validate_connection(connection)
        client = self._client(connection, user=connection.bind_dn, password=bind_password)
        try:
            self._open_bind(connection, client, invalid_is_credentials=False)
            return client
        except Exception:
            self._unbind(client)
            raise

    def _client(
        self,
        connection: DirectoryConnectionRecord,
        *,
        user: str,
        password: str,
    ) -> Any:
        try:
            custom_ca = self._custom_ca_resolver(connection.connection_id)
            tls = Tls(
                validate=ssl.CERT_REQUIRED,
                version=ssl.PROTOCOL_TLS_CLIENT,
                ca_certs_data=custom_ca,
            )
            server = Server(
                connection.host,
                port=connection.port,
                use_ssl=connection.tls_mode == "ldaps",
                tls=tls,
                connect_timeout=connection.connect_timeout_seconds,
                get_info=NONE,
            )
            return self._connection_factory(
                server,
                user=user,
                password=password,
                client_strategy=SAFE_SYNC,
                receive_timeout=connection.operation_timeout_seconds,
                raise_exceptions=False,
                auto_escape=False,
            )
        except Exception:
            raise DirectoryGatewayError("directory_unavailable") from None

    def _open_bind(
        self,
        connection: DirectoryConnectionRecord,
        client: Any,
        *,
        invalid_is_credentials: bool,
    ) -> None:
        try:
            open_result = client.open()
            if open_result is False or getattr(client, "closed", False):
                raise DirectoryGatewayError("directory_unavailable")
            if connection.tls_mode == "start_tls" and not self._result_ok(
                client.start_tls(), client
            ):
                raise DirectoryGatewayError("directory_unavailable")
            if not self._result_ok(client.bind(), client):
                code = self._result_code(client)
                if invalid_is_credentials and code == 49:
                    raise DirectoryGatewayError("invalid_credentials")
                if invalid_is_credentials:
                    raise DirectoryGatewayError("invalid_credentials")
                raise DirectoryGatewayError("directory_unavailable")
        except DirectoryGatewayError:
            raise
        except (LDAPException, OSError, TimeoutError, ssl.SSLError):
            raise DirectoryGatewayError("directory_unavailable") from None
        except Exception:
            raise DirectoryGatewayError("directory_unavailable") from None

    @staticmethod
    def _result_ok(value: Any, client: Any) -> bool:
        if isinstance(value, tuple):
            return bool(value[0])
        if isinstance(value, bool):
            return value
        return bool(value) and LdapDirectoryGateway._result_code(client) == 0

    @staticmethod
    def _result_code(client: Any) -> int | None:
        result = getattr(client, "result", None)
        if isinstance(result, dict) and isinstance(result.get("result"), int):
            return result["result"]
        return None

    def _search(
        self,
        client: Any,
        base_dn: str,
        search_filter: str,
        attributes: tuple[str, ...],
        *,
        size_limit: int,
    ) -> list[dict[str, Any]]:
        try:
            outcome = client.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=list(attributes),
                size_limit=size_limit,
                time_limit=max(1, int(getattr(client, "receive_timeout", 1) or 1)),
            )
            if isinstance(outcome, tuple):
                ok, result, response, _request = outcome
            else:
                ok = bool(outcome)
                result = getattr(client, "result", {})
                response = getattr(client, "response", [])
            code = result.get("result") if isinstance(result, dict) else None
            if not ok and code not in {0, 4}:
                if code == 49:
                    raise DirectoryGatewayError("invalid_credentials")
                raise DirectoryGatewayError("directory_unavailable")
            if not isinstance(response, list):
                raise DirectoryGatewayError("directory_entry_invalid")
            return [
                item
                for item in response
                if isinstance(item, dict) and item.get("type") == "searchResEntry"
            ]
        except DirectoryGatewayError:
            raise
        except (LDAPInvalidFilterError, ValueError, TypeError):
            raise DirectoryGatewayError("directory_entry_invalid") from None
        except (LDAPException, OSError, TimeoutError, ssl.SSLError):
            raise DirectoryGatewayError("directory_unavailable") from None
        except Exception:
            raise DirectoryGatewayError("directory_unavailable") from None

    def _principal(
        self,
        connection: DirectoryConnectionRecord,
        entry: dict[str, Any],
    ) -> DirectoryPrincipal:
        attributes = entry.get("attributes")
        raw_attributes = entry.get("raw_attributes")
        if not isinstance(attributes, Mapping):
            raise DirectoryGatewayError("directory_entry_invalid")
        if not isinstance(raw_attributes, Mapping):
            raw_attributes = {}
        try:
            external_subject = self._external_subject(
                connection,
                attributes,
                raw_attributes,
            )
            username = self._required_text(attributes, connection.login_attribute)
            display_name = self._required_text(
                attributes, connection.display_name_attribute
            )
            email = self._optional_text(attributes, connection.email_attribute)
            groups = self._groups(attributes.get(connection.groups_attribute))
            department = self._optional_text(
                attributes, connection.department_attribute, maximum=500
            )
            title = self._optional_text(
                attributes, connection.title_attribute, maximum=500
            )
            employee_id = self._optional_text(
                attributes, connection.employee_id_attribute, maximum=500
            )
            directory_enabled = None
            if connection.provider_type == "active_directory":
                value = self._first(attributes.get("userAccountControl"))
                if value is None:
                    raise ValueError("userAccountControl is missing")
                directory_enabled = (int(value) & 2) == 0
            return DirectoryPrincipal(
                external_subject=external_subject,
                username=username,
                display_name=display_name,
                email=email,
                groups=groups,
                department=department,
                title=title,
                employee_id=employee_id,
                directory_enabled=directory_enabled,
            )
        except (TypeError, ValueError, UnicodeError):
            raise DirectoryGatewayError("directory_entry_invalid") from None

    @staticmethod
    def _external_subject(
        connection: DirectoryConnectionRecord,
        attributes: dict[str, Any],
        raw_attributes: dict[str, Any],
    ) -> str:
        if connection.provider_type == "active_directory":
            raw = LdapDirectoryGateway._first(
                raw_attributes.get(connection.stable_id_attribute)
            )
            if not isinstance(raw, bytes) or len(raw) != 16:
                raise ValueError("objectGUID is invalid")
            return str(UUID(bytes_le=raw))
        return LdapDirectoryGateway._required_text(
            attributes, connection.stable_id_attribute
        )

    @staticmethod
    def _subject_filter_value(
        connection: DirectoryConnectionRecord, external_subject: str
    ) -> str:
        if connection.provider_type == "active_directory":
            try:
                return escape_bytes(UUID(external_subject).bytes_le)
            except ValueError:
                raise DirectoryGatewayError("directory_entry_invalid") from None
        value = external_subject.strip()
        if not value:
            raise DirectoryGatewayError("directory_entry_invalid")
        return escape_filter_chars(value)

    @staticmethod
    def _groups(value: Any) -> tuple[str, ...]:
        groups: dict[str, str] = {}
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            display = text
            if "=" in text:
                try:
                    parsed = parse_dn(text, escape=True, strip=True)
                    if parsed:
                        display = str(parsed[0][1]).strip()
                except Exception:
                    display = text
            if display:
                groups.setdefault(display.casefold(), display)
        return tuple(groups[key] for key in sorted(groups))

    @staticmethod
    def _first(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
        return value

    @staticmethod
    def _required_text(attributes: dict[str, Any], name: str) -> str:
        value = LdapDirectoryGateway._optional_text(attributes, name)
        if value is None:
            raise ValueError(f"required directory attribute is missing: {name}")
        return value

    @staticmethod
    def _optional_text(
        attributes: dict[str, Any], name: str, *, maximum: int = 1000
    ) -> str | None:
        value = LdapDirectoryGateway._first(attributes.get(name))
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > maximum:
            raise ValueError("directory attribute exceeds its bound")
        return text

    @staticmethod
    def _attributes(connection: DirectoryConnectionRecord) -> tuple[str, ...]:
        names = [
            connection.stable_id_attribute,
            connection.login_attribute,
            connection.display_name_attribute,
            connection.email_attribute,
            connection.groups_attribute,
            connection.department_attribute,
            connection.title_attribute,
            connection.employee_id_attribute,
        ]
        if connection.provider_type == "active_directory":
            names.append("userAccountControl")
        return tuple(dict.fromkeys(names))

    @staticmethod
    def _validate_connection(connection: DirectoryConnectionRecord) -> None:
        if connection.provider_type not in {"active_directory", "ldap"}:
            raise DirectoryGatewayError("directory_entry_invalid")
        if connection.tls_mode not in {"ldaps", "start_tls"}:
            raise DirectoryGatewayError("directory_entry_invalid")
        if not 1 <= connection.port <= 65535:
            raise DirectoryGatewayError("directory_entry_invalid")
        if not 1 <= connection.connect_timeout_seconds <= 30:
            raise DirectoryGatewayError("directory_entry_invalid")
        if not 1 <= connection.operation_timeout_seconds <= 30:
            raise DirectoryGatewayError("directory_entry_invalid")
        try:
            validate_directory_filter(connection.user_object_filter)
        except ValueError:
            raise DirectoryGatewayError("directory_entry_invalid") from None
        if any(
            _ATTRIBUTE_RE.fullmatch(name) is None
            for name in LdapDirectoryGateway._attributes(connection)
        ):
            raise DirectoryGatewayError("directory_entry_invalid")

    @staticmethod
    def _unbind(client: Any) -> None:
        try:
            client.unbind()
        except Exception:
            pass
