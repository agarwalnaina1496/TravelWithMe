"""HTTP contracts for account signup and login."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

EmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]

# bcrypt hard-limits its input to 72 bytes and raises rather than truncating
# beyond it (pyca/bcrypt 4.x); max_length=72 bounds character count, and the
# validator below bounds UTF-8 byte count, since multi-byte characters can
# exceed 72 bytes well under 72 characters.
_MAX_PASSWORD_BYTES = 72


def _validate_password_byte_length(value: str) -> str:
    if len(value.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {_MAX_PASSWORD_BYTES} bytes when UTF-8 encoded.")
    return value


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    password: str = Field(min_length=8, max_length=_MAX_PASSWORD_BYTES)

    _validate_password = field_validator("password")(_validate_password_byte_length)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_BYTES)

    _validate_password = field_validator("password")(_validate_password_byte_length)


class SignupResponse(BaseModel):
    id: UUID
    email: str
    created_at: datetime
    claimed_trip_count: int = Field(
        description="Trips reassigned from this request's guest session to the new account, if any.",
    )


class LoginResponse(BaseModel):
    id: UUID
    email: str
    claimed_trip_count: int = Field(
        description="Trips reassigned from this request's guest session to this account, if any.",
    )
