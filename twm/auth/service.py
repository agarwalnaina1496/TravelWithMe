"""Account signup, login, JWT session resolution, and guest-trip claim."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request, Response

from ..persistence.contracts import DuplicateEmailError, TripRepository, User
from ..persistence.service import hash_guest_token
from ..persistence.settings import DatabaseSettings
from ..telemetry import TelemetryLogger
from .security import UNKNOWN_ACCOUNT_PASSWORD_HASH, InvalidTokenError, hash_password, issue_jwt, verify_jwt, verify_password
from .settings import AuthSettings


class InvalidCredentialsError(Exception):
    """Raised when login email/password do not match a known account."""


@dataclass(frozen=True)
class AuthResult:
    user: User
    claimed_trip_count: int


@dataclass
class AuthService:
    repository: TripRepository
    settings: AuthSettings
    database_settings: DatabaseSettings
    logger: TelemetryLogger

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    def _secret(self) -> str:
        if not self.settings.jwt_secret:
            raise RuntimeError("JWT_SECRET is not configured.")
        return self.settings.jwt_secret

    async def signup(self, email: str, password: str, request: Request) -> AuthResult:
        normalized = self._normalize_email(email)
        try:
            user = await self.repository.create_user(normalized, hash_password(password))
        except DuplicateEmailError:
            self.logger.warning(
                "Rejected signup for an already-registered email.",
                event="be.auth.signup", source="http", outcome="duplicate_email",
            )
            raise
        self.logger.info(
            "Created new account.", event="be.auth.signup", source="http",
            outcome="created", user_id=str(user.id),
        )
        claimed = await self._claim_guest_trips(request, user.id)
        return AuthResult(user=user, claimed_trip_count=claimed)

    async def login(self, email: str, password: str, request: Request, response: Response) -> AuthResult:
        normalized = self._normalize_email(email)
        user = await self.repository.get_user_by_email(normalized)
        # Always pay the bcrypt-verification cost, even for an unknown
        # email, so response timing doesn't disclose which emails are
        # registered (mirrors TWM-64's 404-not-403 anti-disclosure rule).
        password_valid = verify_password(password, user.password_hash if user else UNKNOWN_ACCOUNT_PASSWORD_HASH)
        if user is None or not password_valid:
            self.logger.warning(
                "Rejected login with invalid credentials.",
                event="be.auth.login_failed", source="http",
            )
            raise InvalidCredentialsError()
        token = issue_jwt(user.id, self._secret(), self.settings.jwt_algorithm, self.settings.jwt_expiry_days)
        response.set_cookie(
            key=self.settings.jwt_cookie_name, value=token,
            max_age=self.settings.jwt_expiry_days * 24 * 60 * 60,
            httponly=True, secure=self.settings.jwt_cookie_secure,
            samesite="lax", path="/",
        )
        self.logger.info(
            "Logged in.", event="be.auth.login", source="http", user_id=str(user.id),
        )
        claimed = await self._claim_guest_trips(request, user.id)
        return AuthResult(user=user, claimed_trip_count=claimed)

    async def current_user(self, request: Request) -> User | None:
        token = request.cookies.get(self.settings.jwt_cookie_name)
        if not token:
            return None
        try:
            user_id = verify_jwt(token, self._secret(), self.settings.jwt_algorithm)
        except InvalidTokenError as error:
            self.logger.warning(
                "Rejected JWT on protected request.",
                event="be.auth.jwt_verification_failed", source="http", reason=str(error),
            )
            return None
        return await self.repository.get_user_by_id(user_id)

    async def _claim_guest_trips(self, request: Request, user_id: UUID) -> int:
        """Reassigns the request's own guest session's trips to user_id, if
        any. A missing guest cookie or zero trips are both routine no-ops
        (TWM-179) — only a present-but-expired/unknown cookie is logged as
        a warning, since that's the one unexpected-but-handled case."""
        token = request.cookies.get(self.database_settings.guest_cookie_name)
        if not token:
            return 0
        guest = await self.repository.resolve_guest(hash_guest_token(token), self.database_settings.guest_session_days)
        if guest is None:
            self.logger.warning(
                "Guest cookie present but session expired or unknown at claim time; nothing to claim.",
                event="be.auth.guest_trips_claimed", source="http", user_id=str(user_id), trip_count=0,
            )
            return 0
        claimed = await self.repository.claim_guest_trips(guest.id, user_id)
        self.logger.info(
            "Evaluated guest-trip claim on account authentication.",
            event="be.auth.guest_trips_claimed", source="http", user_id=str(user_id), trip_count=claimed,
        )
        return claimed
