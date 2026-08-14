from __future__ import annotations

from dataclasses import replace
import ssl
from uuid import UUID

import pytest
from ldap3 import Connection, MOCK_SYNC

from atlas_production.infrastructure.ldap_directory_gateway import (
    LdapDirectoryGateway,
    validate_directory_filter,
)
from atlas_production.modules.identity_access.directory_records import (
    DirectoryConnectionRecord,
    DirectoryGatewayError,
)


_GUID = UUID("1f2d3c4b-5a69-7887-96a5-b4c3d2e1f001")
_USER_DN = "CN=Ada Lovelace,OU=People,DC=example,DC=test"
_BIND_DN = "CN=Atlas Bind,OU=Service,DC=example,DC=test"


def connection_record(**changes) -> DirectoryConnectionRecord:
    values = dict(
        connection_id="directory-main",
        display_name="Corporate AD",
        priority=10,
        provider_type="active_directory",
        host="directory.example.test",
        port=636,
        tls_mode="ldaps",
        connect_timeout_seconds=3,
        operation_timeout_seconds=4,
        bind_dn=_BIND_DN,
        user_base_dn="OU=People,DC=example,DC=test",
        user_object_filter="(&(objectCategory=person)(objectClass=user))",
        login_attribute="userPrincipalName",
        stable_id_attribute="objectGUID",
        display_name_attribute="displayName",
        email_attribute="mail",
        groups_attribute="memberOf",
        department_attribute="department",
        title_attribute="title",
        employee_id_attribute="employeeID",
        enabled=True,
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:00+00:00",
    )
    values.update(changes)
    return DirectoryConnectionRecord(**values)


class MockDirectoryFactory:
    def __init__(self, *, duplicate: bool = False) -> None:
        self.passwords: list[str] = []
        self.duplicate = duplicate

    def __call__(self, server, *, user, password, **_kwargs):
        self.passwords.append(password)
        client = Connection(
            server,
            user=user,
            password=password,
            client_strategy=MOCK_SYNC,
            raise_exceptions=False,
            auto_escape=False,
        )
        client.strategy.add_entry(
            _BIND_DN,
            {
                "objectClass": ["person"],
                "cn": "Atlas Bind",
                "userPassword": "bind-secret",
            },
        )
        attributes = {
            "objectClass": ["person", "user"],
            "objectCategory": "person",
            "userPrincipalName": "ada@example.test",
            "objectGUID": _GUID.bytes_le,
            "displayName": "Ada Lovelace",
            "mail": "ada@example.test",
            "memberOf": [
                "CN=Research,OU=Groups,DC=example,DC=test",
                "cn=research,OU=Other,DC=example,DC=test",
                "Standalone",
            ],
            "department": "Analytical Engines",
            "title": "Programmer",
            "employeeID": "E-100",
            "userAccountControl": 512,
            "userPassword": "correct",
        }
        client.strategy.add_entry(_USER_DN, attributes)
        if self.duplicate:
            client.strategy.add_entry(
                "CN=Ada Duplicate,OU=People,DC=example,DC=test",
                {**attributes, "displayName": "Ada Duplicate", "userPassword": "other"},
            )
        return client


def test_filter_parser_rejects_multiple_or_trailing_filters() -> None:
    validate_directory_filter("(&(objectCategory=person)(objectClass=user))")
    with pytest.raises(ValueError):
        validate_directory_filter("(objectClass=user)(mail=*)")


def test_escaped_query_cannot_rewrite_admin_search_filter() -> None:
    filters: list[str] = []

    class CaptureClient:
        result = {"result": 0}
        response: list[dict] = []
        receive_timeout = 2
        closed = False

        def open(self):
            return None

        def bind(self):
            return True

        def search(self, **kwargs):
            filters.append(kwargs["search_filter"])
            return True

        def unbind(self):
            return True

    gateway = LdapDirectoryGateway(
        connection_factory=lambda *_args, **_kwargs: CaptureClient()
    )
    result = gateway.search_users(
        connection_record(),
        "bind-secret",
        query="Ada*)(|(objectClass=*))",
        department=None,
        limit=50,
    )
    assert result == ()
    assert filters == [
        "(&(&(objectCategory=person)(objectClass=user))"
        "(|(userPrincipalName=*Ada\\2a\\29\\28|\\28objectClass=\\2a\\29\\29*)"
        "(displayName=*Ada\\2a\\29\\28|\\28objectClass=\\2a\\29\\29*)"
        "(mail=*Ada\\2a\\29\\28|\\28objectClass=\\2a\\29\\29*)))"
    ]
    filters.clear()
    department_result = gateway.search_users(
        connection_record(),
        "bind-secret",
        query=None,
        department="R&D*)(|(objectClass=*))",
        limit=101,
    )
    assert department_result == ()
    assert filters == [
        "(&(&(objectCategory=person)(objectClass=user))"
        "(department=R&D\\2a\\29\\28|\\28objectClass=\\2a\\29\\29))"
    ]


def test_ad_guid_fields_groups_and_disabled_state_are_normalized() -> None:
    factory = MockDirectoryFactory()
    gateway = LdapDirectoryGateway(connection_factory=factory)
    principal = gateway.fetch_user(connection_record(), "bind-secret", str(_GUID))
    assert principal is not None
    assert principal.external_subject == str(_GUID)
    assert principal.username == "ada@example.test"
    assert principal.display_name == "Ada Lovelace"
    assert principal.email == "ada@example.test"
    assert principal.groups == ("Research", "Standalone")
    assert principal.department == "Analytical Engines"
    assert principal.title == "Programmer"
    assert principal.employee_id == "E-100"
    assert principal.directory_enabled is True

    disabled_factory = MockDirectoryFactory()
    disabled_gateway = LdapDirectoryGateway(connection_factory=disabled_factory)
    original = disabled_factory.__call__

    def disabled(server, **kwargs):
        client = original(server, **kwargs)
        client.strategy.entries[_USER_DN]["userAccountControl"] = [b"514"]
        return client

    disabled_gateway._connection_factory = disabled
    disabled_principal = disabled_gateway.fetch_user(
        connection_record(), "bind-secret", str(_GUID)
    )
    assert disabled_principal is not None
    assert disabled_principal.directory_enabled is False


def test_exact_subject_lookup_distinguishes_zero_one_and_duplicate() -> None:
    gateway = LdapDirectoryGateway(connection_factory=MockDirectoryFactory())
    assert gateway.fetch_user(
        connection_record(), "bind-secret", str(UUID(int=0))
    ) is None
    assert gateway.fetch_user(connection_record(), "bind-secret", str(_GUID)) is not None

    duplicate = LdapDirectoryGateway(
        connection_factory=MockDirectoryFactory(duplicate=True)
    )
    with pytest.raises(DirectoryGatewayError, match="directory_entry_invalid"):
        duplicate.fetch_user(connection_record(), "bind-secret", str(_GUID))


def test_user_password_is_sent_once_only_to_selected_entry_bind() -> None:
    factory = MockDirectoryFactory()
    gateway = LdapDirectoryGateway(connection_factory=factory)
    principal = gateway.authenticate(
        connection_record(), "bind-secret", str(_GUID), "correct"
    )
    assert principal.username == "ada@example.test"
    assert factory.passwords == ["bind-secret", "correct"]

    with pytest.raises(DirectoryGatewayError, match="invalid_credentials") as error:
        gateway.authenticate(
            connection_record(), "bind-secret", str(_GUID), "wrong-password"
        )
    assert "wrong-password" not in repr(error.value)
    assert _USER_DN not in repr(error.value)


def test_start_tls_and_certificate_failures_are_safe_and_fail_closed() -> None:
    events: list[str] = []
    servers = []

    class StartTlsFailure:
        result = {"result": 0}
        receive_timeout = 2

        def open(self):
            events.append("open")
            return True

        def start_tls(self):
            events.append("start_tls")
            return False

        def bind(self):
            events.append("bind")
            return True

        def unbind(self):
            return True

    def start_tls_factory(server, **_kwargs):
        servers.append(server)
        return StartTlsFailure()

    gateway = LdapDirectoryGateway(connection_factory=start_tls_factory)
    with pytest.raises(DirectoryGatewayError, match="directory_unavailable"):
        gateway.test_connection(
            replace(connection_record(), tls_mode="start_tls", port=389),
            "bind-secret",
        )
    assert len(servers) == 1
    assert servers[0].ssl is False
    assert events == ["open", "start_tls"]

    def certificate_failure(*_args, **_kwargs):
        raise ssl.SSLError("certificate diagnostic with CN=private")

    certificate_gateway = LdapDirectoryGateway(
        connection_factory=certificate_failure
    )
    with pytest.raises(DirectoryGatewayError, match="directory_unavailable") as error:
        certificate_gateway.test_connection(connection_record(), "bind-secret")
    assert "private" not in repr(error.value)
    assert "bind-secret" not in repr(error.value)


def test_plain_ldap_opens_then_binds_without_tls_or_custom_ca() -> None:
    events: list[str] = []
    servers = []

    class CaptureClient:
        result = {"result": 0}
        response: list[dict] = []
        receive_timeout = 2
        closed = False

        def open(self):
            events.append("open")
            return True

        def bind(self):
            events.append("bind")
            return True

        def start_tls(self):
            raise AssertionError("plain LDAP must not invoke StartTLS")

        def search(self, **_kwargs):
            events.append("search")
            return True

        def unbind(self):
            events.append("unbind")
            return True

    def capture_factory(server, **_kwargs):
        servers.append(server)
        return CaptureClient()

    def reject_ca_resolution(_connection_id):
        raise AssertionError("plain LDAP must not resolve custom CA")

    gateway = LdapDirectoryGateway(
        custom_ca_resolver=reject_ca_resolution,
        connection_factory=capture_factory,
    )
    gateway.test_connection(
        connection_record(
            provider_type="ldap",
            tls_mode="plain",
            port=389,
            user_object_filter="(objectClass=person)",
            login_attribute="uid",
            stable_id_attribute="entryUUID",
        ),
        "bind-secret",
    )

    assert len(servers) == 1
    assert servers[0].port == 389
    assert servers[0].ssl is False
    assert events == ["open", "bind", "search", "unbind"]


def test_plain_active_directory_preserves_object_guid_lookup() -> None:
    factory = MockDirectoryFactory()
    gateway = LdapDirectoryGateway(connection_factory=factory)
    principal = gateway.fetch_user(
        replace(connection_record(), tls_mode="plain", port=389),
        "bind-secret",
        str(_GUID),
    )
    assert principal is not None
    assert principal.external_subject == str(_GUID)
    assert principal.username == "ada@example.test"
    assert factory.passwords == ["bind-secret"]
