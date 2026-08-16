"""Password hashing and JWT issuance/verification for account auth."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt


class InvalidTokenError(Exception):
    """Raised for a missing, malformed, expired, or tampered JWT."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# A fixed, precomputed hash to verify against when no account exists, so
# an unregistered-email login costs the same bcrypt-verification time as a
# wrong-password one — otherwise the response-time gap discloses which
# emails are registered.
UNKNOWN_ACCOUNT_PASSWORD_HASH = hash_password("unregistered-account-timing-equalizer")


def issue_jwt(user_id: UUID, secret: str, algorithm: str, expiry_days: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(days=expiry_days)}
    return jwt.encode(payload, secret, algorithm=algorithm)


def verify_jwt(token: str, secret: str, algorithm: str) -> UUID:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as error:
        raise InvalidTokenError("expired") from error
    except jwt.InvalidTokenError as error:
        raise InvalidTokenError("invalid") from error
    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError) as error:
        raise InvalidTokenError("invalid") from error
