"""Runtime settings for application persistence."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseSettings:
    url: str | None
    schema: str = "twm_app"
    guest_cookie_name: str = "twm_guest"
    guest_cookie_secure: bool = True
    guest_session_days: int = 180

    @classmethod
    def load(cls) -> "DatabaseSettings":
        environment = os.getenv("ENVIRONMENT", "prod")
        return cls(
            url=os.getenv("APP_DATABASE_URL"),
            schema=os.getenv("APP_DATABASE_SCHEMA", "twm_app"),
            guest_cookie_name=os.getenv("GUEST_COOKIE_NAME", "twm_guest"),
            guest_cookie_secure=os.getenv(
                "GUEST_COOKIE_SECURE", "true" if environment == "prod" else "false"
            ).lower() == "true",
            guest_session_days=int(os.getenv("GUEST_SESSION_DAYS", "180")),
        )
