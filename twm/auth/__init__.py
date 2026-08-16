"""Email/password account authentication capability."""

from .service import AuthService, InvalidCredentialsError
from .settings import AuthSettings

__all__ = ["AuthService", "AuthSettings", "InvalidCredentialsError"]
