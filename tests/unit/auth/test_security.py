"""Password hashing and JWT issuance/verification (TWM-178)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from twm.auth.security import UNKNOWN_ACCOUNT_PASSWORD_HASH, InvalidTokenError, hash_password, issue_jwt, verify_jwt, verify_password


def test_hash_password_never_returns_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"


def test_verify_password_succeeds_for_the_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_fails_for_the_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("wrong password", hashed) is False


def test_verify_password_fails_for_a_malformed_hash() -> None:
    assert verify_password("anything", "not-a-real-hash") is False


def test_unknown_account_password_hash_is_a_valid_bcrypt_hash_that_never_matches() -> None:
    assert verify_password("any-password-a-caller-might-try", UNKNOWN_ACCOUNT_PASSWORD_HASH) is False


def test_issue_jwt_resolves_to_the_same_user_id() -> None:
    user_id = uuid4()

    token = issue_jwt(user_id, secret="test-secret-at-least-32-bytes-long!!", algorithm="HS256", expiry_days=1)

    assert verify_jwt(token, secret="test-secret-at-least-32-bytes-long!!", algorithm="HS256") == user_id


def test_verify_jwt_rejects_a_tampered_token() -> None:
    user_id = uuid4()
    token = issue_jwt(user_id, secret="test-secret-at-least-32-bytes-long!!", algorithm="HS256", expiry_days=1)

    with pytest.raises(InvalidTokenError):
        verify_jwt(token, secret="a-different-secret-at-least-32-bytes", algorithm="HS256")


def test_verify_jwt_rejects_an_expired_token() -> None:
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"sub": str(uuid4()), "iat": now - timedelta(days=2), "exp": now - timedelta(days=1)},
        "test-secret-at-least-32-bytes-long!!", algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        verify_jwt(expired, secret="test-secret-at-least-32-bytes-long!!", algorithm="HS256")


def test_verify_jwt_rejects_a_missing_subject_claim() -> None:
    now = datetime.now(timezone.utc)
    malformed = jwt.encode({"iat": now, "exp": now + timedelta(days=1)}, "test-secret-at-least-32-bytes-long!!", algorithm="HS256")

    with pytest.raises(InvalidTokenError):
        verify_jwt(malformed, secret="test-secret-at-least-32-bytes-long!!", algorithm="HS256")
