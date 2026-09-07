"""Async Postgres implementation of owned trip persistence."""

import copy
import json
import re
from dataclasses import replace
from typing import Any
from uuid import UUID

import asyncpg

from .contracts import (
    BLOB_STATE_FIELDS,
    LIFECYCLE_COLUMN_FIELDS,
    DuplicateEmailError,
    GuestSession,
    ItineraryVersionRecord,
    RecommendationRecord,
    TripCommandRecord,
    TripOwner,
    TripRecord,
    User,
    VersionConflictError,
)

# Branches split out of trips.trip_state into dedicated tables (TWM-158).
# itinerary_state is handled separately below — it is pointer-only
# (status, current_version) with the full result composed from
# itinerary_versions only on the full read path (never for commands).
_BLOB_BRANCHES = ("matcher_state", "planner_state", "booking_setup")
_ITINERARY_BRANCH = "itinerary_state"

# GET /trips has no cursor yet (the UI shows every trip); this is a generous
# safety bound, not a real page size. A request above it is clamped, not
# rejected.
_TRIP_LIST_LIMIT_DEFAULT = 200
_TRIP_LIST_LIMIT_MAX = 200

# TWM-158/TWM-191: one round trip for every branch-table read, instead of a
# SELECT per branch. Anchored on a literal so the row always exists and each
# branch LEFT JOINs in (or stays NULL when that trip has no such row).
_BRANCH_COMPOSE_SQL = """
    SELECT
        m.state AS matcher_state,
        p.state AS planner_state,
        b.state AS booking_setup,
        i.trip_id AS itinerary_present,
        i.status AS itinerary_status,
        i.current_version AS itinerary_current_version
    FROM (SELECT $1::uuid AS tid) x
    LEFT JOIN {schema}.matcher_state m ON m.trip_id = x.tid
    LEFT JOIN {schema}.planner_state p ON p.trip_id = x.tid
    LEFT JOIN {schema}.booking_setup b ON b.trip_id = x.tid
    LEFT JOIN {schema}.itinerary_state i ON i.trip_id = x.tid
"""


def _owner_clause(owner: TripOwner, index: int, *, alias: str = "") -> str:
    """A trip is reachable by user_id once claimed, or by an unclaimed
    guest_session_id otherwise (TWM-179) — always exactly one placeholder,
    so callers can splice this into a query without shifting other
    positional params."""
    prefix = f"{alias}." if alias else ""
    if owner.user_id is not None:
        return f"{prefix}user_id=${index}"
    return f"{prefix}guest_session_id=${index} AND {prefix}user_id IS NULL"


def _owner_value(owner: TripOwner) -> UUID:
    return owner.user_id if owner.user_id is not None else owner.guest_session_id


def _row_count(result: str) -> int:
    """asyncpg's Connection.execute() returns a command tag like 'UPDATE 3'."""
    return int(result.rsplit(" ", 1)[-1])


def _json_object(value: str | dict[str, Any]) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _blob_state(trip_state: dict[str, Any]) -> dict[str, Any]:
    """The subset of trip_state persisted to the trips.trip_state jsonb blob
    (TWM-191) — everything that is neither a lifecycle column nor a dedicated
    branch table."""
    return {key: trip_state[key] for key in BLOB_STATE_FIELDS if key in trip_state}


def _lifecycle_values(trip_state: dict[str, Any]) -> tuple[str, str, str | None]:
    """(stage, status, active_agent) for the trips columns. stage/status fall
    back to their NOT-NULL defaults for a bare `POST /trips` create that
    never ran canonical_state(); active_agent is genuinely nullable (None
    once the plan is frozen)."""
    return (
        trip_state.get("stage") or "new",
        trip_state.get("status") or "free",
        trip_state.get("active_agent"),
    )


def _with_lifecycle(trip_state: dict[str, Any], row: asyncpg.Record) -> dict[str, Any]:
    """Recompose the lifecycle columns back into the in-memory trip_state
    dict so command handlers, TripResponse and TripSummary are unaffected by
    the column move (TWM-191)."""
    for field in LIFECYCLE_COLUMN_FIELDS:
        trip_state[field] = row[field]
    return trip_state


def _record(row: asyncpg.Record) -> TripRecord:
    return TripRecord(
        id=row["id"], guest_session_id=row["guest_session_id"], user_id=row["user_id"], title=row["title"],
        product_mode=row["product_mode"],
        trip_state=_with_lifecycle(_json_object(row["trip_state"]), row),
        ui_state=_json_object(row["ui_state"]),
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

    async def claim_guest_trips(self, guest_session_id: UUID, user_id: UUID) -> int:
        """Reassigns every trip still scoped to guest_session_id to user_id,
        and every trip_commands row alongside it — otherwise a pre-claim
        idempotency key resent after login would resolve by the new user_id
        scope, miss the still-guest-scoped row, and re-execute instead of
        replaying. Filters on user_id IS NULL in both tables, so a repeat
        call (double-login) is a safe no-op rather than re-claiming or
        erroring (TWM-179)."""
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    f"UPDATE {self.schema}.trips SET user_id=$2, updated_at=now() WHERE guest_session_id=$1 AND user_id IS NULL",
                    guest_session_id, user_id)
                await connection.execute(
                    f"UPDATE {self.schema}.trip_commands SET user_id=$2 WHERE guest_session_id=$1 AND user_id IS NULL",
                    guest_session_id, user_id)
        return _row_count(result)

    async def list_trips(self, owner: TripOwner, limit: int = _TRIP_LIST_LIMIT_DEFAULT) -> list[TripRecord]:
        """GET /trips (TWM-182): batched, summary-scoped composition — the
        generic per-trip compose (matcher/planner/booking_setup branch reads
        plus full itinerary-result composition) previously ran once per trip
        here, an N+1 pattern whose output the router's _summary() then
        discarded almost entirely. TripSummary only needs itinerary status
        and a cheap planner_state-derived signal, so this fetches just those
        two branches, batched across every trip id in two queries total. The
        `limit` (TWM-191) is a generous safety bound — the UI shows all
        trips and has no cursor yet."""
        limit = max(1, min(limit, _TRIP_LIST_LIMIT_MAX))
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                f"SELECT * FROM {self.schema}.trips WHERE {_owner_clause(owner, 1)} ORDER BY updated_at DESC LIMIT $2",
                _owner_value(owner), limit)
            trip_ids = [row["id"] for row in rows]
            planner_by_id, itinerary_status_by_id = await self._batch_list_summary_branches(connection, trip_ids)
            records = []
            for row in rows:
                record = _record(row)
                state = dict(record.trip_state)
                planner = planner_by_id.get(record.id)
                if planner is not None:
                    state["planner_state"] = planner
                status = itinerary_status_by_id.get(record.id)
                if status is not None:
                    state["itinerary_state"] = {"status": status}
                records.append(replace(record, trip_state=state))
            return records

    async def _batch_list_summary_branches(
        self, connection: asyncpg.Connection, trip_ids: list[UUID]
    ) -> tuple[dict[UUID, dict[str, Any]], dict[UUID, str | None]]:
        if not trip_ids:
            return {}, {}
        planner_rows = await connection.fetch(
            f"SELECT trip_id, state FROM {self.schema}.planner_state WHERE trip_id = ANY($1::uuid[])", trip_ids)
        planner_by_id = {row["trip_id"]: _json_object(row["state"]) for row in planner_rows}
        itinerary_rows = await connection.fetch(
            f"SELECT trip_id, status FROM {self.schema}.itinerary_state WHERE trip_id = ANY($1::uuid[])", trip_ids)
        itinerary_status_by_id = {row["trip_id"]: row["status"] for row in itinerary_rows}
        return planner_by_id, itinerary_status_by_id

    async def create_trip(self, guest_id: UUID, user_id: UUID | None, title: str, product_mode: str, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord:
        from ..services.trip_commands.state import canonical_state, touched_branches

        stage, status, active_agent = _lifecycle_values(trip_state)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"""INSERT INTO {self.schema}.trips
                    (guest_session_id,user_id,title,product_mode,trip_state,ui_state,stage,status,active_agent)
                    VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9) RETURNING *""",
                    guest_id, user_id, title, product_mode,
                    json.dumps(_blob_state(trip_state)), json.dumps(ui_state), stage, status, active_agent)
                touched = touched_branches(trip_state, canonical_state({}))
                await self._write_branch_tables(connection, row["id"], trip_state, frozenset(touched))
                composed = await self._compose_trip_state(
                    connection, row["id"], _record(row).trip_state, with_itinerary_result=True
                )
                return replace(_record(row), trip_state=composed)

    async def get_trip(self, owner: TripOwner, trip_id: UUID) -> TripRecord | None:
        """Full compose — includes the itinerary result blob. Used where the
        caller actually needs the plan content (flight-search / trusted-action
        payload derivation); the trips.py routes use get_trip_core."""
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT * FROM {self.schema}.trips WHERE id=$1 AND {_owner_clause(owner, 2)}",
                trip_id, _owner_value(owner))
            if not row:
                return None
            record = _record(row)
            composed = await self._compose_trip_state(
                connection, trip_id, record.trip_state, with_itinerary_result=True
            )
            return replace(record, trip_state=composed)

    async def get_trip_core(self, owner: TripOwner, trip_id: UUID) -> TripRecord | None:
        """Lean compose (TWM-191) — the branch tables plus the itinerary
        *pointer* (status + current_version number), never a query against
        itinerary_versions. Used by POST .../commands, /recommendations,
        /board and the /itinerary existence check: none of them render the
        composed itinerary_state.current_version to the client, and command
        handlers only test current_version for truthiness."""
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT * FROM {self.schema}.trips WHERE id=$1 AND {_owner_clause(owner, 2)}",
                trip_id, _owner_value(owner))
            if not row:
                return None
            record = _record(row)
            composed = await self._compose_trip_state(
                connection, trip_id, record.trip_state, with_itinerary_result=False
            )
            return replace(record, trip_state=composed)

    async def _mutate(self, query: str, owner: TripOwner, trip_id: UUID, expected_version: int, *values: Any) -> TripRecord | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(query, trip_id, _owner_value(owner), expected_version, *values)
                if row:
                    record = _record(row)
                    composed = await self._compose_trip_state(
                        connection, trip_id, record.trip_state, with_itinerary_result=False
                    )
                    return replace(record, trip_state=composed)
                current = await connection.fetchval(
                    f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND {_owner_clause(owner, 2)}",
                    trip_id, _owner_value(owner))
                if current is None:
                    return None
                raise VersionConflictError(current)

    async def replace_trip(self, owner: TripOwner, trip_id: UUID, expected_version: int, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord | None:
        from ..services.trip_commands.state import canonical_state, touched_branches

        stage, status, active_agent = _lifecycle_values(trip_state)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"""UPDATE {self.schema}.trips
                    SET trip_state=$4::jsonb,ui_state=$5::jsonb,stage=$6,status=$7,active_agent=$8,
                        version=version+1,updated_at=now()
                    WHERE id=$1 AND {_owner_clause(owner, 2)} AND version=$3 RETURNING *""",
                    trip_id, _owner_value(owner), expected_version,
                    json.dumps(_blob_state(trip_state)), json.dumps(ui_state), stage, status, active_agent)
                if not row:
                    current = await connection.fetchval(
                        f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND {_owner_clause(owner, 2)}",
                        trip_id, _owner_value(owner))
                    if current is None:
                        return None
                    raise VersionConflictError(current)
                touched = touched_branches(trip_state, canonical_state({}))
                await self._write_branch_tables(connection, trip_id, trip_state, frozenset(touched))
                composed = await self._compose_trip_state(
                    connection, trip_id, _record(row).trip_state, with_itinerary_result=False
                )
                return replace(_record(row), trip_state=composed)

    async def rename_trip(self, owner: TripOwner, trip_id: UUID, expected_version: int, title: str) -> TripRecord | None:
        return await self._mutate(
            f"""UPDATE {self.schema}.trips SET title=$4,version=version+1,updated_at=now()
            WHERE id=$1 AND {_owner_clause(owner, 2)} AND version=$3 RETURNING *""",
            owner, trip_id, expected_version, title)

    async def update_ui_state(self, owner: TripOwner, trip_id: UUID, expected_version: int, ui_state: dict[str, Any]) -> TripRecord | None:
        return await self._mutate(
            f"""UPDATE {self.schema}.trips SET ui_state=$4::jsonb,version=version+1,updated_at=now()
            WHERE id=$1 AND {_owner_clause(owner, 2)} AND version=$3 RETURNING *""",
            owner, trip_id, expected_version, json.dumps(ui_state))

    async def get_command(self, owner: TripOwner, trip_id: UUID, idempotency_key: UUID) -> TripCommandRecord | None:
        row = await self.pool.fetchrow(
            f"""SELECT request_hash,response FROM {self.schema}.trip_commands
            WHERE {_owner_clause(owner, 1)} AND trip_id=$2 AND idempotency_key=$3""",
            _owner_value(owner), trip_id, idempotency_key,
        )
        if not row:
            return None
        response = await self._hydrate_command_response(self.pool, trip_id, _json_object(row["response"]))
        return TripCommandRecord(row["request_hash"], response)

    async def get_latest_recommendation(self, owner: TripOwner, trip_id: UUID) -> RecommendationRecord | None:
        row = await self.pool.fetchrow(
            f"""SELECT r.* FROM {self.schema}.matcher_recommendations r
            JOIN {self.schema}.trips t ON t.id = r.trip_id
            WHERE r.trip_id=$1 AND {_owner_clause(owner, 2, alias="t")}
            ORDER BY r.version DESC LIMIT 1""",
            trip_id, _owner_value(owner),
        )
        return _recommendation_record(row) if row else None

    async def trip_ids_with_recommendations(self, owner: TripOwner, trip_ids: list[UUID]) -> set[UUID]:
        """TWM-190: batched existence check (never the records themselves) for
        GET /trips's has_recommendation summary signal — mirrors the
        planner/itinerary batching in list_trips's _batch_list_summary_branches,
        one query regardless of trip count rather than a per-trip fetch."""
        if not trip_ids:
            return set()
        rows = await self.pool.fetch(
            f"""SELECT DISTINCT r.trip_id FROM {self.schema}.matcher_recommendations r
            JOIN {self.schema}.trips t ON t.id = r.trip_id
            WHERE r.trip_id = ANY($1::uuid[]) AND {_owner_clause(owner, 2, alias="t")}""",
            trip_ids, _owner_value(owner),
        )
        return {row["trip_id"] for row in rows}

    async def get_current_itinerary(self, owner: TripOwner, trip_id: UUID) -> ItineraryVersionRecord | None:
        row = await self.pool.fetchrow(
            f"""SELECT v.* FROM {self.schema}.itinerary_versions v
            JOIN {self.schema}.itinerary_state s ON s.trip_id = v.trip_id AND s.current_version = v.version
            JOIN {self.schema}.trips t ON t.id = v.trip_id
            WHERE v.trip_id=$1 AND {_owner_clause(owner, 2, alias="t")}""",
            trip_id, _owner_value(owner),
        )
        return _itinerary_version_record(row) if row else None

    async def commit_command(
        self, owner: TripOwner, trip_id: UUID, expected_version: int,
        idempotency_key: UUID, request_hash: str, trip_state: dict[str, Any],
        response_trip_state: dict[str, Any], response: dict[str, Any],
        touched_branches: frozenset[str],
        new_recommendation: dict[str, Any] | None = None,
    ) -> TripRecord | TripCommandRecord | None:
        stage, status, active_agent = _lifecycle_values(trip_state)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                prior = await connection.fetchrow(
                    f"""SELECT request_hash,response FROM {self.schema}.trip_commands
                    WHERE {_owner_clause(owner, 1)} AND trip_id=$2 AND idempotency_key=$3""",
                    _owner_value(owner), trip_id, idempotency_key,
                )
                if prior:
                    return TripCommandRecord(
                        prior["request_hash"],
                        await self._hydrate_command_response(connection, trip_id, _json_object(prior["response"])),
                    )
                row = await connection.fetchrow(
                    f"""UPDATE {self.schema}.trips
                    SET trip_state=$4::jsonb,stage=$5,status=$6,active_agent=$7,version=version+1,updated_at=now()
                    WHERE id=$1 AND {_owner_clause(owner, 2)} AND version=$3 RETURNING *""",
                    trip_id, _owner_value(owner), expected_version,
                    json.dumps(_blob_state(trip_state)), stage, status, active_agent,
                )
                if not row:
                    prior = await connection.fetchrow(
                        f"""SELECT request_hash,response FROM {self.schema}.trip_commands
                        WHERE {_owner_clause(owner, 1)} AND trip_id=$2 AND idempotency_key=$3""",
                        _owner_value(owner), trip_id, idempotency_key,
                    )
                    if prior:
                        return TripCommandRecord(
                            prior["request_hash"],
                            await self._hydrate_command_response(connection, trip_id, _json_object(prior["response"])),
                        )
                    current = await connection.fetchval(
                        f"SELECT version FROM {self.schema}.trips WHERE id=$1 AND {_owner_clause(owner, 2)}",
                        trip_id, _owner_value(owner),
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
                stored_response = dict(response)
                response_record = _record(row).__dict__.copy()
                # TWM-191: the archived idempotency response carries the
                # shaped trip_state minus the (immutable, re-derivable)
                # itinerary result blob — recomposed from itinerary_versions
                # on replay so the response is still byte-identical.
                response_record["trip_state"] = _strip_itinerary_result(response_trip_state)
                stored_response["trip"] = response_record
                await connection.execute(
                    f"""INSERT INTO {self.schema}.trip_commands
                    (guest_session_id,user_id,trip_id,idempotency_key,request_hash,response)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb)""",
                    owner.guest_session_id, owner.user_id, trip_id, idempotency_key, request_hash,
                    json.dumps(stored_response, default=str),
                )
                return _record(row)

    async def _hydrate_command_response(
        self, executor: Any, trip_id: UUID, response: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-inline the itinerary result stripped by commit_command, so an
        idempotency replay returns the exact original TripCommandResponse."""
        trip = response.get("trip")
        itinerary = (trip or {}).get("trip_state", {}).get("itinerary_state")
        current_version = itinerary.get("current_version") if isinstance(itinerary, dict) else None
        if not (isinstance(current_version, dict) and "result" not in current_version and current_version.get("version")):
            return response
        row = await executor.fetchrow(
            f"""SELECT version, source_guide_revision, result FROM {self.schema}.itinerary_versions
            WHERE trip_id=$1 AND version=$2""",
            trip_id, current_version["version"],
        )
        if row:
            itinerary["current_version"] = {
                "version": row["version"],
                "source_guide_revision": row["source_guide_revision"],
                "result": _json_object(row["result"]),
            }
        return response

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
            # TWM-158: every active current_version is archived to
            # itinerary_versions on write (trip_state keeps only the pointer).
            await connection.execute(
                f"""INSERT INTO {self.schema}.itinerary_versions (trip_id,version,source_guide_revision,result)
                VALUES ($1,$2,$3,$4::jsonb) ON CONFLICT (trip_id, version) DO NOTHING""",
                trip_id, current_version["version"], current_version["source_guide_revision"],
                json.dumps(current_version["result"]),
            )

    async def _compose_trip_state(
        self, connection: asyncpg.Connection, trip_id: UUID, base_state: dict[str, Any], *, with_itinerary_result: bool
    ) -> dict[str, Any]:
        state = dict(base_state)
        row = await connection.fetchrow(_BRANCH_COMPOSE_SQL.format(schema=self.schema), trip_id)
        for branch in _BLOB_BRANCHES:
            if row[branch] is not None:
                state[branch] = _json_object(row[branch])
        if row["itinerary_present"] is not None:
            state[_ITINERARY_BRANCH] = await self._compose_itinerary_branch(
                connection, trip_id, row["itinerary_status"], row["itinerary_current_version"],
                with_result=with_itinerary_result,
            )
        return state

    async def _compose_itinerary_branch(
        self, connection: asyncpg.Connection, trip_id: UUID,
        status: str | None, current_version: int | None, *, with_result: bool,
    ) -> dict[str, Any]:
        if current_version is None:
            return {"status": status, "current_version": None}
        if not with_result:
            # Pointer only — the number, no itinerary_versions query. Command
            # handlers only test this for truthiness and the routes that use
            # the lean path never render it to the client (TWM-191).
            return {"status": status, "current_version": current_version}
        version_row = await connection.fetchrow(
            f"""SELECT version, source_guide_revision, result FROM {self.schema}.itinerary_versions
            WHERE trip_id=$1 AND version=$2""",
            trip_id, current_version,
        )
        if not version_row:
            return {"status": status, "current_version": None}
        return {
            "status": status,
            "current_version": {
                "version": version_row["version"],
                "source_guide_revision": version_row["source_guide_revision"],
                "result": _json_object(version_row["result"]),
            },
        }


def _strip_itinerary_result(trip_state: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of the shaped trip_state with the itinerary result blob
    reduced to its version pointer — the caller keeps using the original for
    the live response, so this must not mutate it."""
    itinerary = trip_state.get("itinerary_state")
    current_version = itinerary.get("current_version") if isinstance(itinerary, dict) else None
    if not (isinstance(current_version, dict) and "result" in current_version):
        return trip_state
    stripped = copy.deepcopy(trip_state)
    stripped["itinerary_state"]["current_version"] = {"version": current_version["version"]}
    return stripped
