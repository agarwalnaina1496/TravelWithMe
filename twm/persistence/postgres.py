"""Async Postgres implementation of owned trip persistence."""

import json
import re
from typing import Any
from uuid import UUID

import asyncpg

from .contracts import GuestSession, ItineraryVersionRecord, RecommendationRecord, TripCommandRecord, TripRecord, VersionConflictError


def _json_object(value: str | dict[str, Any]) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _record(row: asyncpg.Record) -> TripRecord:
    return TripRecord(
        id=row["id"], guest_session_id=row["guest_session_id"], title=row["title"],
        product_mode=row["product_mode"], trip_state=_json_object(row["trip_state"]), ui_state=_json_object(row["ui_state"]),
        version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _recommendation_record(row: asyncpg.Record) -> RecommendationRecord:
    return RecommendationRecord(
        trip_id=row["trip_id"], version=row["version"], status=row["status"], message=row["message"],
        trip_type=row["trip_type"], options=_json_value(row["options"]),
        traveler_criteria=_json_value(row["traveler_criteria"]) if row["traveler_criteria"] is not None else None,
        constraint_adjustment_suggestions=_json_value(row["constraint_adjustment_suggestions"]) if row["constraint_adjustment_suggestions"] is not None else None,
        agent_meta=_json_object(row["agent_meta"]), created_at=row["created_at"],
    )


def _itinerary_version_record(row: asyncpg.Record) -> ItineraryVersionRecord:
    return ItineraryVersionRecord(
        trip_id=row["trip_id"], version=row["version"], source_guide_revision=row["source_guide_revision"],
        result=_json_object(row["result"]), created_at=row["created_at"],
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

    async def update_ui_state(self, guest_id: UUID, trip_id: UUID, expected_version: int, ui_state: dict[str, Any]) -> TripRecord | None:
        return await self._mutate(
            f"""UPDATE {self.schema}.trips SET ui_state=$4::jsonb,version=version+1,updated_at=now()
            WHERE id=$1 AND guest_session_id=$2 AND version=$3 RETURNING *""",
            guest_id, trip_id, expected_version, json.dumps(ui_state))

    async def get_command(self, guest_id: UUID, trip_id: UUID, idempotency_key: UUID) -> TripCommandRecord | None:
        row = await self.pool.fetchrow(
            f"""SELECT request_hash,response FROM {self.schema}.trip_commands
            WHERE guest_session_id=$1 AND trip_id=$2 AND idempotency_key=$3""",
            guest_id, trip_id, idempotency_key,
        )
        return TripCommandRecord(row["request_hash"], _json_object(row["response"])) if row else None

    async def get_latest_recommendation(self, guest_id: UUID, trip_id: UUID) -> RecommendationRecord | None:
        row = await self.pool.fetchrow(
            f"""SELECT r.* FROM {self.schema}.matcher_recommendations r
            JOIN {self.schema}.trips t ON t.id = r.trip_id
            WHERE r.trip_id=$1 AND t.guest_session_id=$2
            ORDER BY r.version DESC LIMIT 1""",
            trip_id, guest_id,
        )
        return _recommendation_record(row) if row else None

    async def list_itinerary_versions(self, guest_id: UUID, trip_id: UUID) -> list[ItineraryVersionRecord]:
        rows = await self.pool.fetch(
            f"""SELECT r.* FROM {self.schema}.itinerary_versions r
            JOIN {self.schema}.trips t ON t.id = r.trip_id
            WHERE r.trip_id=$1 AND t.guest_session_id=$2
            ORDER BY r.version ASC""",
            trip_id, guest_id,
        )
        return [_itinerary_version_record(row) for row in rows]

    async def commit_command(
        self, guest_id: UUID, trip_id: UUID, expected_version: int,
        idempotency_key: UUID, request_hash: str, trip_state: dict[str, Any],
        response_trip_state: dict[str, Any], response: dict[str, Any],
        new_recommendation: dict[str, Any] | None = None,
        new_itinerary_version: dict[str, Any] | None = None,
    ) -> TripRecord | TripCommandRecord | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                prior = await connection.fetchrow(
                    f"""SELECT request_hash,response FROM {self.schema}.trip_commands
                    WHERE guest_session_id=$1 AND trip_id=$2 AND idempotency_key=$3""",
                    guest_id, trip_id, idempotency_key,
                )
                if prior:
                    return TripCommandRecord(prior["request_hash"], _json_object(prior["response"]))
                row = await connection.fetchrow(
                    f"""UPDATE {self.schema}.trips SET trip_state=$4::jsonb,version=version+1,updated_at=now()
                    WHERE id=$1 AND guest_session_id=$2 AND version=$3 RETURNING *""",
                    trip_id, guest_id, expected_version, json.dumps(trip_state),
                )
                if not row:
                    prior = await connection.fetchrow(
                        f"""SELECT request_hash,response FROM {self.schema}.trip_commands
                        WHERE guest_session_id=$1 AND trip_id=$2 AND idempotency_key=$3""",
                        guest_id, trip_id, idempotency_key,
                    )
                    if prior:
                        return TripCommandRecord(
                            prior["request_hash"], _json_object(prior["response"])
                        )
                    current = await connection.fetchval(
                        f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND guest_session_id=$2",
                        trip_id, guest_id,
                    )
                    if current is None:
                        return None
                    raise VersionConflictError(current)
                if new_recommendation is not None:
                    await connection.execute(
                        f"""INSERT INTO {self.schema}.matcher_recommendations
                        (trip_id,version,status,message,trip_type,options,traveler_criteria,constraint_adjustment_suggestions,agent_meta)
                        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb)""",
                        trip_id, new_recommendation["version"], new_recommendation["status"],
                        new_recommendation["message"], new_recommendation.get("trip_type"),
                        json.dumps(new_recommendation.get("options") or []),
                        json.dumps(new_recommendation.get("traveler_criteria")) if new_recommendation.get("traveler_criteria") is not None else None,
                        json.dumps(new_recommendation.get("constraint_adjustment_suggestions")) if new_recommendation.get("constraint_adjustment_suggestions") is not None else None,
                        json.dumps(new_recommendation["agent_meta"]),
                    )
                if new_itinerary_version is not None:
                    await connection.execute(
                        f"""INSERT INTO {self.schema}.itinerary_versions
                        (trip_id,version,source_guide_revision,result)
                        VALUES ($1,$2,$3,$4::jsonb)""",
                        trip_id, new_itinerary_version["version"], new_itinerary_version["source_guide_revision"],
                        json.dumps(new_itinerary_version["result"]),
                    )
                stored_response = dict(response)
                response_record = _record(row).__dict__.copy()
                response_record["trip_state"] = response_trip_state
                stored_response["trip"] = response_record
                await connection.execute(
                    f"""INSERT INTO {self.schema}.trip_commands
                    (guest_session_id,trip_id,idempotency_key,request_hash,response)
                    VALUES ($1,$2,$3,$4,$5::jsonb)""",
                    guest_id, trip_id, idempotency_key, request_hash,
                    json.dumps(stored_response, default=str),
                )
                return _record(row)
