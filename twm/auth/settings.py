"""Runtime settings for JWT-based account authentication."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str | None
    jwt_algorithm: str = "HS256"
    jwt_cookie_name: str = "twm_auth"
    jwt_cookie_secure: bool = True
    jwt_expiry_days: int = 30

    @classmethod
    def load(cls) -> "AuthSettings":
        environment = os.getenv("ENVIRONMENT", "prod")
        return cls(
            jwt_secret=os.getenv("JWT_SECRET"),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            jwt_cookie_name=os.getenv("JWT_COOKIE_NAME", "twm_auth"),
            jwt_cookie_secure=os.getenv(
                "JWT_COOKIE_SECURE", "true" if environment == "prod" else "false"
            ).lower() == "true",
            jwt_expiry_days=int(os.getenv("JWT_EXPIRY_DAYS", "30")),
        )
