"""Async Postgres implementation of owned trip persistence."""

import json
import re
from dataclasses import replace
from typing import Any
from uuid import UUID

import asyncpg

from .contracts import DuplicateEmailError, GuestSession, ItineraryVersionRecord, RecommendationRecord, TripCommandRecord, TripRecord, User, VersionConflictError

# Branches split out of trips.trip_state into dedicated tables (TWM-158).
# itinerary_state is handled separately below — it is pointer-only
# (status, current_version) with the full result composed from
# itinerary_versions / itinerary_proposed_revisions.
_BLOB_BRANCHES = ("matcher_state", "planner_state", "logistics_state")
_ITINERARY_BRANCH = "itinerary_state"

# trips.trip_state now holds only these non-touchable fields; everything
# else lives in the dedicated branch tables above.
_CORE_STATE_FIELDS = ("status", "stage", "active_agent", "trip_context", "advisor_state")


def _json_object(value: str | dict[str, Any]) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _core_state(trip_state: dict[str, Any]) -> dict[str, Any]:
    return {key: trip_state[key] for key in _CORE_STATE_FIELDS if key in trip_state}


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


def _user_record(row: asyncpg.Record) -> User:
    return User(id=row["id"], email=row["email"], password_hash=row["password_hash"], created_at=row["created_at"])


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

    async def create_user(self, email: str, password_hash: str) -> User:
        try:
            row = await self.pool.fetchrow(
                f"INSERT INTO {self.schema}.users (email,password_hash) VALUES ($1,$2) RETURNING *",
                email, password_hash)
        except asyncpg.UniqueViolationError as error:
            raise DuplicateEmailError(email) from error
        return _user_record(row)

    async def get_user_by_email(self, email: str) -> User | None:
        row = await self.pool.fetchrow(f"SELECT * FROM {self.schema}.users WHERE email=$1", email)
        return _user_record(row) if row else None

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        row = await self.pool.fetchrow(f"SELECT * FROM {self.schema}.users WHERE id=$1", user_id)
        return _user_record(row) if row else None

    async def list_trips(self, guest_id: UUID) -> list[TripRecord]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(f"SELECT * FROM {self.schema}.trips WHERE guest_session_id=$1 ORDER BY updated_at DESC", guest_id)
            records = []
            for row in rows:
                record = _record(row)
                composed = await self._compose_trip_state(connection, record.id, record.trip_state)
                records.append(replace(record, trip_state=composed))
            return records

    async def create_trip(self, guest_id: UUID, title: str, product_mode: str, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"""INSERT INTO {self.schema}.trips (guest_session_id,title,product_mode,trip_state,ui_state)
                    VALUES ($1,$2,$3,$4::jsonb,$5::jsonb) RETURNING *""",
                    guest_id, title, product_mode, json.dumps(_core_state(trip_state)), json.dumps(ui_state))
                present = frozenset(key for key in _BLOB_BRANCHES + (_ITINERARY_BRANCH,) if key in trip_state)
                await self._write_branch_tables(connection, row["id"], trip_state, present)
                composed = await self._compose_trip_state(connection, row["id"], _core_state(trip_state))
                return replace(_record(row), trip_state=composed)

    async def get_trip(self, guest_id: UUID, trip_id: UUID) -> TripRecord | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(f"SELECT * FROM {self.schema}.trips WHERE id=$1 AND guest_session_id=$2", trip_id, guest_id)
            if not row:
                return None
            record = _record(row)
            composed = await self._compose_trip_state(connection, trip_id, record.trip_state)
            return replace(record, trip_state=composed)

    async def _mutate(self, query: str, guest_id: UUID, trip_id: UUID, expected_version: int, *values: Any) -> TripRecord | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(query, trip_id, guest_id, expected_version, *values)
                if row:
                    record = _record(row)
                    composed = await self._compose_trip_state(connection, trip_id, record.trip_state)
                    return replace(record, trip_state=composed)
                current = await connection.fetchval(f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND guest_session_id=$2", trip_id, guest_id)
                if current is None:
                    return None
                raise VersionConflictError(current)

    async def replace_trip(self, guest_id: UUID, trip_id: UUID, expected_version: int, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"""UPDATE {self.schema}.trips SET trip_state=$4::jsonb,ui_state=$5::jsonb,version=version+1,updated_at=now()
                    WHERE id=$1 AND guest_session_id=$2 AND version=$3 RETURNING *""",
                    trip_id, guest_id, expected_version, json.dumps(_core_state(trip_state)), json.dumps(ui_state))
                if not row:
                    current = await connection.fetchval(f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND guest_session_id=$2", trip_id, guest_id)
                    if current is None:
                        return None
                    raise VersionConflictError(current)
                present = frozenset(key for key in _BLOB_BRANCHES + (_ITINERARY_BRANCH,) if key in trip_state)
                await self._write_branch_tables(connection, trip_id, trip_state, present)
                composed = await self._compose_trip_state(connection, trip_id, _core_state(trip_state))
                return replace(_record(row), trip_state=composed)

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

    async def get_current_itinerary(self, guest_id: UUID, trip_id: UUID) -> ItineraryVersionRecord | None:
        row = await self.pool.fetchrow(
            f"""SELECT v.* FROM {self.schema}.itinerary_versions v
            JOIN {self.schema}.itinerary_state s ON s.trip_id = v.trip_id AND s.current_version = v.version
            JOIN {self.schema}.trips t ON t.id = v.trip_id
            WHERE v.trip_id=$1 AND t.guest_session_id=$2""",
            trip_id, guest_id,
        )
        return _itinerary_version_record(row) if row else None

    async def commit_command(
        self, guest_id: UUID, trip_id: UUID, expected_version: int,
        idempotency_key: UUID, request_hash: str, trip_state: dict[str, Any],
        response_trip_state: dict[str, Any], response: dict[str, Any],
        touched_branches: frozenset[str],
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
                    trip_id, guest_id, expected_version, json.dumps(_core_state(trip_state)),
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
                await self._write_branch_tables(connection, trip_id, trip_state, touched_branches)
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
                        VALUES ($1,$2,$3,$4::jsonb) ON CONFLICT (trip_id, version) DO NOTHING""",
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

    async def _write_branch_tables(
        self, connection: asyncpg.Connection, trip_id: UUID, trip_state: dict[str, Any], touched_branches: frozenset[str]
    ) -> None:
        for branch in _BLOB_BRANCHES:
            if branch not in touched_branches:
                continue
            await connection.execute(
                f"""INSERT INTO {self.schema}.{branch} (trip_id, state) VALUES ($1,$2::jsonb)
                ON CONFLICT (trip_id) DO UPDATE SET state=EXCLUDED.state, updated_at=now()""",
                trip_id, json.dumps(trip_state[branch]),
            )
        if _ITINERARY_BRANCH in touched_branches:
            await self._write_itinerary_branch(connection, trip_id, trip_state[_ITINERARY_BRANCH])

    async def _write_itinerary_branch(self, connection: asyncpg.Connection, trip_id: UUID, itinerary: dict[str, Any]) -> None:
        current_version = itinerary.get("current_version")
        await connection.execute(
            f"""INSERT INTO {self.schema}.itinerary_state (trip_id, status, current_version)
            VALUES ($1,$2,$3)
            ON CONFLICT (trip_id) DO UPDATE SET status=EXCLUDED.status, current_version=EXCLUDED.current_version, updated_at=now()""",
            trip_id, itinerary.get("status"), current_version["version"] if current_version else None,
        )
        if current_version is not None:
            # Every active current_version is archived here too now, not only
            # the superseded one (see new_itinerary_version above) — TWM-158.
            await connection.execute(
                f"""INSERT INTO {self.schema}.itinerary_versions (trip_id,version,source_guide_revision,result)
                VALUES ($1,$2,$3,$4::jsonb) ON CONFLICT (trip_id, version) DO NOTHING""",
                trip_id, current_version["version"], current_version["source_guide_revision"],
                json.dumps(current_version["result"]),
            )
        proposed = itinerary.get("proposed_revision")
        if proposed is not None:
            await connection.execute(
                f"""INSERT INTO {self.schema}.itinerary_proposed_revisions
                (trip_id,base_version,result,affected_days,changes,triggered_by)
                VALUES ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb)
                ON CONFLICT (trip_id) DO UPDATE SET base_version=EXCLUDED.base_version, result=EXCLUDED.result,
                affected_days=EXCLUDED.affected_days, changes=EXCLUDED.changes, triggered_by=EXCLUDED.triggered_by, updated_at=now()""",
                trip_id, proposed["base_version"], json.dumps(proposed["result"]),
                json.dumps(proposed.get("affected_days") or []), json.dumps(proposed.get("changes") or []),
                json.dumps(proposed["triggered_by"]),
            )
        else:
            await connection.execute(f"DELETE FROM {self.schema}.itinerary_proposed_revisions WHERE trip_id=$1", trip_id)

    async def _compose_trip_state(self, connection: asyncpg.Connection, trip_id: UUID, core_state: dict[str, Any]) -> dict[str, Any]:
        state = dict(core_state)
        for branch in _BLOB_BRANCHES:
            row = await connection.fetchrow(f"SELECT state FROM {self.schema}.{branch} WHERE trip_id=$1", trip_id)
            if row:
                state[branch] = _json_object(row["state"])
        itinerary = await self._compose_itinerary_branch(connection, trip_id)
        if itinerary is not None:
            state[_ITINERARY_BRANCH] = itinerary
        return state

    async def _compose_itinerary_branch(self, connection: asyncpg.Connection, trip_id: UUID) -> dict[str, Any] | None:
        pointer = await connection.fetchrow(f"SELECT status, current_version FROM {self.schema}.itinerary_state WHERE trip_id=$1", trip_id)
        if pointer is None:
            return None
        itinerary: dict[str, Any] = {"status": pointer["status"], "current_version": None, "proposed_revision": None}
        if pointer["current_version"] is not None:
            version_row = await connection.fetchrow(
                f"""SELECT version, source_guide_revision, result FROM {self.schema}.itinerary_versions
                WHERE trip_id=$1 AND version=$2""",
                trip_id, pointer["current_version"],
            )
            if version_row:
                itinerary["current_version"] = {
                    "version": version_row["version"],
                    "source_guide_revision": version_row["source_guide_revision"],
                    "result": _json_object(version_row["result"]),
                }
        proposed_row = await connection.fetchrow(
            f"""SELECT base_version, result, affected_days, changes, triggered_by
            FROM {self.schema}.itinerary_proposed_revisions WHERE trip_id=$1""",
            trip_id,
        )
        if proposed_row:
            itinerary["proposed_revision"] = {
                "version": proposed_row["base_version"] + 1,
                "base_version": proposed_row["base_version"],
                "result": _json_object(proposed_row["result"]),
                "affected_days": _json_value(proposed_row["affected_days"]),
                "changes": _json_value(proposed_row["changes"]),
                "triggered_by": _json_object(proposed_row["triggered_by"]),
            }
        return itinerary
