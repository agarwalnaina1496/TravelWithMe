"""A minimal in-memory stand-in for asyncpg.Pool, scoped exactly to the
queries PostgresTripRepository issues (TWM-158). There is no real-Postgres
integration harness in this repository yet; this fake lets the branch-table
split (which tables get read/written) be verified at the SQL-call level
without one.
"""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4


def _dumps(value):
    return json.dumps(value)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, db: "FakeDatabase"):
        self.db = db

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *args):
        return self.db.dispatch(query, args)

    async def fetch(self, query, *args):
        result = self.db.dispatch(query, args, many=True)
        return result or []

    async def fetchval(self, query, *args):
        row = self.db.dispatch(query, args)
        if row is None:
            return None
        return next(iter(row.values()))

    async def execute(self, query, *args):
        self.db.dispatch(query, args)
        return "OK"


class _Acquire:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return FakeConnection(self.db)

    async def __aexit__(self, *exc):
        return False


class FakeDatabase:
    """schema-qualified in-memory tables + a log of which tables were written."""

    def __init__(self, schema: str):
        self.schema = schema
        self.trips: dict[UUID, dict] = {}
        self.branch_tables = {"matcher_state": {}, "planner_state": {}, "logistics_state": {}}
        self.itinerary_state: dict[UUID, dict] = {}
        self.itinerary_versions: dict[tuple[UUID, int], dict] = {}
        self.itinerary_proposed_revisions: dict[UUID, dict] = {}
        self.trip_commands: dict[tuple, dict] = {}
        self.matcher_recommendations: dict[tuple[UUID, int], dict] = {}
        self.written_tables: set[str] = set()

    def pool(self):
        return FakePool(self)

    def q(self, table: str) -> str:
        return f"{self.schema}.{table}"

    def dispatch(self, query: str, args, many: bool = False):
        q = " ".join(query.split())

        # --- trip_commands ---
        if q.startswith(f"SELECT request_hash,response FROM {self.q('trip_commands')}"):
            guest_id, trip_id, idem = args
            row = self.trip_commands.get((guest_id, trip_id, idem))
            return dict(row) if row else None
        if q.startswith(f"INSERT INTO {self.q('trip_commands')}"):
            guest_id, trip_id, idem, request_hash, response = args
            self.trip_commands[(guest_id, trip_id, idem)] = {
                "request_hash": request_hash, "response": response,
            }
            self.written_tables.add("trip_commands")
            return None

        # --- trips ---
        if q.startswith(f"UPDATE {self.q('trips')} SET trip_state=$4::jsonb,version=version+1"):
            trip_id, guest_id, expected_version, trip_state = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id or trip["version"] != expected_version:
                return None
            trip["trip_state"] = trip_state
            trip["version"] += 1
            trip["updated_at"] = datetime.now(timezone.utc)
            self.written_tables.add("trips")
            return dict(trip)
        if q.startswith(f"UPDATE {self.q('trips')} SET trip_state=$4::jsonb,ui_state=$5::jsonb"):
            trip_id, guest_id, expected_version, trip_state, ui_state = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id or trip["version"] != expected_version:
                return None
            trip["trip_state"] = trip_state
            trip["ui_state"] = ui_state
            trip["version"] += 1
            trip["updated_at"] = datetime.now(timezone.utc)
            self.written_tables.add("trips")
            return dict(trip)
        if q.startswith(f"UPDATE {self.q('trips')} SET title=$4"):
            trip_id, guest_id, expected_version, title = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id or trip["version"] != expected_version:
                return None
            trip["title"] = title
            trip["version"] += 1
            trip["updated_at"] = datetime.now(timezone.utc)
            self.written_tables.add("trips")
            return dict(trip)
        if q.startswith(f"UPDATE {self.q('trips')} SET ui_state=$4::jsonb"):
            trip_id, guest_id, expected_version, ui_state = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id or trip["version"] != expected_version:
                return None
            trip["ui_state"] = ui_state
            trip["version"] += 1
            trip["updated_at"] = datetime.now(timezone.utc)
            self.written_tables.add("trips")
            return dict(trip)
        if q.startswith(f"SELECT version FROM {self.q('trips')}"):
            trip_id, guest_id = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id:
                return None
            return {"version": trip["version"]}
        if q.startswith(f"INSERT INTO {self.q('trips')}"):
            guest_id, title, product_mode, trip_state, ui_state = args
            trip_id = uuid4()
            now = datetime.now(timezone.utc)
            trip = {
                "id": trip_id, "guest_session_id": guest_id, "title": title, "product_mode": product_mode,
                "trip_state": trip_state, "ui_state": ui_state, "version": 1, "created_at": now, "updated_at": now,
            }
            self.trips[trip_id] = trip
            self.written_tables.add("trips")
            return dict(trip)
        if q.startswith(f"SELECT * FROM {self.q('trips')} WHERE id=$1"):
            trip_id, guest_id = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id:
                return None
            return dict(trip)
        if q.startswith(f"SELECT * FROM {self.q('trips')} WHERE guest_session_id=$1"):
            (guest_id,) = args
            return [dict(t) for t in self.trips.values() if t["guest_session_id"] == guest_id]

        # --- blob branch tables (matcher_state/planner_state/logistics_state) ---
        for branch in self.branch_tables:
            if q.startswith(f"INSERT INTO {self.q(branch)} (trip_id, state)"):
                trip_id, state = args
                self.branch_tables[branch][trip_id] = {"state": state}
                self.written_tables.add(branch)
                return None
            if q.startswith(f"SELECT state FROM {self.q(branch)} WHERE trip_id=$1"):
                (trip_id,) = args
                row = self.branch_tables[branch].get(trip_id)
                return dict(row) if row else None

        # --- itinerary_state (pointer) ---
        if q.startswith(f"INSERT INTO {self.q('itinerary_state')} (trip_id, status, current_version)"):
            trip_id, status, current_version = args
            self.itinerary_state[trip_id] = {"status": status, "current_version": current_version}
            self.written_tables.add("itinerary_state")
            return None
        if q.startswith(f"SELECT status, current_version FROM {self.q('itinerary_state')}"):
            (trip_id,) = args
            row = self.itinerary_state.get(trip_id)
            return dict(row) if row else None

        # --- itinerary_versions ---
        if q.startswith(f"INSERT INTO {self.q('itinerary_versions')}"):
            trip_id, version, source_guide_revision, result = args
            key = (trip_id, version)
            if key not in self.itinerary_versions:
                self.itinerary_versions[key] = {
                    "trip_id": trip_id, "version": version,
                    "source_guide_revision": source_guide_revision, "result": result,
                    "created_at": datetime.now(timezone.utc),
                }
            self.written_tables.add("itinerary_versions")
            return None
        if q.startswith(f"SELECT version, source_guide_revision, result FROM {self.q('itinerary_versions')}"):
            trip_id, version = args
            row = self.itinerary_versions.get((trip_id, version))
            return dict(row) if row else None
        if q.startswith(f"SELECT r.* FROM {self.q('itinerary_versions')}"):
            trip_id, guest_id = args
            trip = self.trips.get(trip_id)
            if not trip or trip["guest_session_id"] != guest_id:
                return []
            rows = [v for (t, _v), v in self.itinerary_versions.items() if t == trip_id]
            return sorted(rows, key=lambda r: r["version"])

        # --- itinerary_proposed_revisions ---
        if q.startswith(f"INSERT INTO {self.q('itinerary_proposed_revisions')}"):
            trip_id, base_version, result, affected_days, changes, triggered_by = args
            self.itinerary_proposed_revisions[trip_id] = {
                "base_version": base_version, "result": result,
                "affected_days": affected_days, "changes": changes, "triggered_by": triggered_by,
            }
            self.written_tables.add("itinerary_proposed_revisions")
            return None
        if q.startswith(f"DELETE FROM {self.q('itinerary_proposed_revisions')}"):
            (trip_id,) = args
            existed = self.itinerary_proposed_revisions.pop(trip_id, None)
            if existed is not None:
                self.written_tables.add("itinerary_proposed_revisions")
            return None
        if q.startswith(f"SELECT base_version, result, affected_days, changes, triggered_by FROM {self.q('itinerary_proposed_revisions')}"):
            (trip_id,) = args
            row = self.itinerary_proposed_revisions.get(trip_id)
            return dict(row) if row else None

        # --- matcher_recommendations ---
        if q.startswith(f"INSERT INTO {self.q('matcher_recommendations')}"):
            trip_id, version = args[0], args[1]
            self.matcher_recommendations[(trip_id, version)] = args
            self.written_tables.add("matcher_recommendations")
            return None
        if q.startswith(f"SELECT r.* FROM {self.q('matcher_recommendations')}"):
            return None

        raise AssertionError(f"FakeDatabase: unhandled query: {q}")


class FakePool:
    def __init__(self, db: FakeDatabase):
        self.db = db

    def acquire(self):
        return _Acquire(self.db)

    async def fetchrow(self, query, *args):
        return self.db.dispatch(query, args)

    async def fetch(self, query, *args):
        return self.db.dispatch(query, args, many=True) or []
