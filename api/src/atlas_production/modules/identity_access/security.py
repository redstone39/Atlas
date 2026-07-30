import base64
import hmac
from hashlib import pbkdf2_hmac, sha256
from secrets import token_bytes


PBKDF2_ITERATIONS = 260_000


def password_digest(password: str) -> str:
    salt = token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${iterations}${salt}${digest}".format(
        iterations=PBKDF2_ITERATIONS,
        salt=base64.urlsafe_b64encode(salt).decode("ascii"),
        digest=base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_digest: str | None) -> bool:
    if not stored_digest:
        return False
    if not stored_digest.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, iterations, salt_value, digest_value = stored_digest.split("$", 3)
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def agent_token_digest(token: str) -> str:
    return sha256(f"atlas-production-agent:{token}".encode("utf-8")).hexdigest()


def invite_token_digest(token: str) -> str:
    return sha256(f"atlas-production-invite:{token}".encode("utf-8")).hexdigest()
