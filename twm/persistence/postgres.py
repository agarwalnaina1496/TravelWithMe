"""Async Postgres implementation of owned trip persistence."""

import json
import re
from typing import Any
from uuid import UUID

import asyncpg

from .contracts import GuestSession, TripRecord, VersionConflictError


def _json_object(value: str | dict[str, Any]) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def _record(row: asyncpg.Record) -> TripRecord:
    return TripRecord(
        id=row["id"], guest_session_id=row["guest_session_id"], title=row["title"],
        product_mode=row["product_mode"], trip_state=_json_object(row["trip_state"]), ui_state=_json_object(row["ui_state"]),
        version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


class PostgresTripRepository:
    def __init__(self, pool: asyncpg.Pool, schema: str):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError("Invalid application database schema name.")
        self.pool = pool
        self.schema = schema

    async def resolve_guest(self, token_hash: str, lifetime_days: int) -> GuestSession | None:
        row = await self.pool.fetchrow(
            f"""UPDATE {self.schema}.guest_sessions SET expires_at=now()+($2*interval '1 day'), last_seen_at=now()
            WHERE token_hash=$1 AND expires_at>now() RETURNING id, expires_at""", token_hash, lifetime_days)
        return GuestSession(**dict(row)) if row else None

    async def create_guest(self, token_hash: str, lifetime_days: int) -> GuestSession:
        row = await self.pool.fetchrow(
            f"INSERT INTO {self.schema}.guest_sessions (token_hash,expires_at) VALUES ($1,now()+($2*interval '1 day')) RETURNING id,expires_at",
            token_hash, lifetime_days)
        return GuestSession(**dict(row))

    async def list_trips(self, guest_id: UUID) -> list[TripRecord]:
        rows = await self.pool.fetch(f"SELECT * FROM {self.schema}.trips WHERE guest_session_id=$1 ORDER BY updated_at DESC", guest_id)
        return [_record(row) for row in rows]

    async def create_trip(self, guest_id: UUID, title: str, product_mode: str, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord:
        row = await self.pool.fetchrow(
            f"""INSERT INTO {self.schema}.trips (guest_session_id,title,product_mode,trip_state,ui_state)
            VALUES ($1,$2,$3,$4::jsonb,$5::jsonb) RETURNING *""",
            guest_id, title, product_mode, json.dumps(trip_state), json.dumps(ui_state))
        return _record(row)

    async def get_trip(self, guest_id: UUID, trip_id: UUID) -> TripRecord | None:
        row = await self.pool.fetchrow(f"SELECT * FROM {self.schema}.trips WHERE id=$1 AND guest_session_id=$2", trip_id, guest_id)
        return _record(row) if row else None

    async def _mutate(self, query: str, guest_id: UUID, trip_id: UUID, expected_version: int, *values: Any) -> TripRecord | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(query, trip_id, guest_id, expected_version, *values)
                if row:
                    return _record(row)
                current = await connection.fetchval(f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND guest_session_id=$2", trip_id, guest_id)
                if current is None:
                    return None
                raise VersionConflictError(current)

    async def replace_trip(self, guest_id: UUID, trip_id: UUID, expected_version: int, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord | None:
        return await self._mutate(
            f"""UPDATE {self.schema}.trips SET trip_state=$4::jsonb,ui_state=$5::jsonb,version=version+1,updated_at=now()
            WHERE id=$1 AND guest_session_id=$2 AND version=$3 RETURNING *""",
            guest_id, trip_id, expected_version, json.dumps(trip_state), json.dumps(ui_state))

    async def rename_trip(self, guest_id: UUID, trip_id: UUID, expected_version: int, title: str) -> TripRecord | None:
        return await self._mutate(
            f"""UPDATE {self.schema}.trips SET title=$4,version=version+1,updated_at=now()
            WHERE id=$1 AND guest_session_id=$2 AND version=$3 RETURNING *""",
            guest_id, trip_id, expected_version, title)
