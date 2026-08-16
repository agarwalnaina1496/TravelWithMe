"""Guest identity and trip persistence orchestration."""

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Request, Response

from .contracts import GuestSession, TripRepository
from .settings import DatabaseSettings


def hash_guest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class TripPersistenceService:
    repository: TripRepository
    settings: DatabaseSettings

    async def guest(self, request: Request, response: Response) -> GuestSession:
        token = request.cookies.get(self.settings.guest_cookie_name)
        guest = None
        if token:
            guest = await self.repository.resolve_guest(hash_guest_token(token), self.settings.guest_session_days)
        if guest is None:
            token = secrets.token_urlsafe(32)
            guest = await self.repository.create_guest(hash_guest_token(token), self.settings.guest_session_days)
        response.set_cookie(
            key=self.settings.guest_cookie_name, value=token,
            max_age=self.settings.guest_session_days * 24 * 60 * 60,
            httponly=True, secure=self.settings.guest_cookie_secure,
            samesite="lax", path="/",
        )
        return guest
