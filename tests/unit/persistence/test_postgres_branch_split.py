"""TWM-158: matcher/planner/itinerary/logistics branch-table split.

Uses the in-memory FakeDatabase (postgres_fakes.py) since this repository
has no real-Postgres integration harness — these tests verify SQL-call
behavior (which tables a commit writes, how get_trip composes the
in-memory dict from multiple tables) at that boundary instead.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.unit.persistence.postgres_fakes import FakeDatabase
from twm.persistence.contracts import TripCommandRecord, TripOwner, VersionConflictError
from twm.persistence.postgres import PostgresTripRepository

SCHEMA = "twm_app"


def _owner(guest_id) -> TripOwner:
    return TripOwner(guest_session_id=guest_id, user_id=None)


def _seed_trip(db: FakeDatabase, guest_id, *, core_state=None, version=1):
    trip_id = uuid4()
    now = datetime.now(timezone.utc)
    db.trips[trip_id] = {
        "id": trip_id, "guest_session_id": guest_id, "user_id": None, "title": "Trip", "product_mode": "self_led",
        "trip_state": __import__("json").dumps(core_state or {}), "ui_state": "{}",
        "version": version, "created_at": now, "updated_at": now,
    }
    return trip_id


def _repository(db: FakeDatabase) -> PostgresTripRepository:
    return PostgresTripRepository(pool=db.pool(), schema=SCHEMA)


def test_scout_only_commit_writes_only_trips_and_matcher_state():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)

    committed = asyncio.run(repository.commit_command(
        _owner(guest_id), trip_id, expected_version=1,
        idempotency_key=uuid4(), request_hash="hash",
        trip_state={
            "status": "free", "stage": "new", "active_agent": "scout", "trip_context": {"destination": "Rishikesh"},
            "matcher_state": {"conversation_context": {"awaiting": None}},
        },
        response_trip_state={"trip_id": str(trip_id)}, response={"message": None, "agent_meta": None},
        touched_branches=frozenset({"matcher_state"}),
    ))

    assert committed.version == 2
    assert db.written_tables == {"trips", "trip_commands", "matcher_state"}
    assert "planner_state" not in db.written_tables
    assert "itinerary_state" not in db.written_tables
    assert "logistics_state" not in db.written_tables


def test_commit_command_replays_a_stored_response_for_a_reused_idempotency_key():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)
    idempotency_key = uuid4()
    base_state = {"status": "free", "stage": "new", "active_agent": "scout", "trip_context": {}}

    first = asyncio.run(repository.commit_command(
        _owner(guest_id), trip_id, 1, idempotency_key, "hash", base_state,
        {"trip_id": str(trip_id)}, {"message": None, "agent_meta": None}, frozenset(),
    ))
    db.written_tables.clear()
    replay = asyncio.run(repository.commit_command(
        _owner(guest_id), trip_id, 1, idempotency_key, "hash", base_state,
        {"trip_id": str(trip_id)}, {"message": None, "agent_meta": None}, frozenset(),
    ))

    assert first.version == 2
    assert isinstance(replay, TripCommandRecord)
    assert replay.request_hash == "hash"
    assert db.written_tables == set()


def test_commit_command_raises_version_conflict_on_stale_expected_version():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id, version=3)

    with pytest.raises(VersionConflictError) as excinfo:
        asyncio.run(repository.commit_command(
            _owner(guest_id), trip_id, expected_version=1, idempotency_key=uuid4(), request_hash="hash",
            trip_state={"status": "free", "stage": "new", "active_agent": "scout", "trip_context": {}},
            response_trip_state={}, response={"message": None, "agent_meta": None}, touched_branches=frozenset(),
        ))
    assert excinfo.value.current_version == 3


def test_selected_option_survives_a_commit_and_reload_round_trip():
    """Regression test: selected_option is a core trip_state field (small,
    always-present, alongside stage/active_agent/trip_context) — not one
    of the dedicated branch tables — so it must round-trip through
    _CORE_STATE_FIELDS the same way trip_context already does. Forgetting
    to list it there would let it compute correctly in memory and appear
    in that one command's response, but never actually persist — a
    silent, hard-to-notice data loss this test exists to catch."""
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id, core_state={
        "status": "free", "stage": "recommended", "active_agent": None, "trip_context": {},
    })

    asyncio.run(repository.commit_command(
        _owner(guest_id), trip_id, expected_version=1,
        idempotency_key=uuid4(), request_hash="hash",
        trip_state={
            "status": "free", "stage": "matched", "active_agent": None,
            "trip_context": {"destinations": ["Goa"]},
            "selected_option": {"type": "single", "id": "goa", "name": "Goa"},
        },
        response_trip_state={"trip_id": str(trip_id)}, response={"message": None, "agent_meta": None},
        touched_branches=frozenset(),
    ))

    trip = asyncio.run(repository.get_trip(_owner(guest_id), trip_id))

    assert trip.trip_state["selected_option"] == {"type": "single", "id": "goa", "name": "Goa"}


def test_get_trip_composes_blob_branches_from_dedicated_tables():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id, core_state={"status": "free", "stage": "planning", "trip_context": {"destinations": ["Goa"]}})
    db.branch_tables["planner_state"][trip_id] = {"state": __import__("json").dumps({"places": ["Baga Beach"]})}
    db.branch_tables["logistics_state"][trip_id] = {"state": __import__("json").dumps({"anchors": []})}

    trip = asyncio.run(repository.get_trip(_owner(guest_id), trip_id))

    assert trip.trip_state["stage"] == "planning"
    assert trip.trip_state["trip_context"] == {"destinations": ["Goa"]}
    assert trip.trip_state["planner_state"] == {"places": ["Baga Beach"]}
    assert trip.trip_state["logistics_state"] == {"anchors": []}
    assert "matcher_state" not in trip.trip_state
    assert "itinerary_state" not in trip.trip_state


# TWM-182: list_trips() previously composed the full trip_state per trip
# (matcher/planner/logistics branch reads plus full itinerary-result
# composition) via the same path as get_trip — an N+1 pattern whose output
# the router's _summary() then discarded almost entirely. It now batches
# just the two branches TripSummary actually needs (planner_state,
# itinerary_state status) across every trip id in the list, independent of
# trip count.
def test_list_trips_batches_summary_branch_reads_instead_of_querying_per_trip():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_ids = [_seed_trip(db, guest_id, core_state={"stage": "planning"}) for _ in range(5)]
    for trip_id in trip_ids:
        db.branch_tables["planner_state"][trip_id] = {"state": __import__("json").dumps({"conversation_context": {"awaiting": "trip_duration"}})}
        db.itinerary_state[trip_id] = {"status": "ready", "current_version": None}

    db.query_log.clear()
    trips = asyncio.run(repository.list_trips(_owner(guest_id)))

    assert len(trips) == 5
    # 1 trips-table SELECT + 1 batched planner_state SELECT + 1 batched
    # itinerary_state SELECT — never scales with trip count.
    assert len(db.query_log) == 3
    assert not any("matcher_state" in q or "logistics_state" in q or "itinerary_versions" in q for q in db.query_log)


def test_list_trips_composes_planner_state_and_itinerary_status_per_trip():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    gathering_id = _seed_trip(db, guest_id, core_state={"stage": "planning", "trip_context": {"destinations": ["Udaipur"]}})
    db.branch_tables["planner_state"][gathering_id] = {"state": __import__("json").dumps({"conversation_context": {"awaiting": "trip_duration"}})}
    draft_id = _seed_trip(db, guest_id, core_state={"stage": "planning", "trip_context": {"destinations": ["Coorg"]}})
    db.branch_tables["planner_state"][draft_id] = {"state": __import__("json").dumps({
        "conversation_context": {"awaiting": None}, "places": [{"name": "Abbey Falls"}], "day_plan": [{"day": 1}],
    })}
    db.itinerary_state[draft_id] = {"status": "ready", "current_version": None}
    empty_id = _seed_trip(db, guest_id, core_state={"stage": "new"})

    trips = asyncio.run(repository.list_trips(_owner(guest_id)))
    by_id = {t.id: t for t in trips}

    assert by_id[gathering_id].trip_state["planner_state"]["conversation_context"]["awaiting"] == "trip_duration"
    assert "itinerary_state" not in by_id[gathering_id].trip_state

    assert by_id[draft_id].trip_state["planner_state"]["day_plan"] == [{"day": 1}]
    assert by_id[draft_id].trip_state["itinerary_state"] == {"status": "ready"}

    assert "planner_state" not in by_id[empty_id].trip_state
    assert "itinerary_state" not in by_id[empty_id].trip_state


def test_get_trip_composes_itinerary_state_current_version_and_proposed_revision():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)
    db.itinerary_state[trip_id] = {"status": "ready", "current_version": 1}
    db.itinerary_versions[(trip_id, 1)] = {
        "trip_id": trip_id, "version": 1, "source_guide_revision": 5,
        "result": __import__("json").dumps({"final_itinerary": {"days": []}}),
        "created_at": datetime.now(timezone.utc),
    }
    db.itinerary_proposed_revisions[trip_id] = {
        "base_version": 1, "result": __import__("json").dumps({"final_itinerary": {"days": ["revised"]}}),
        "affected_days": __import__("json").dumps([1]), "changes": __import__("json").dumps(["Day 1: revised"]),
        "triggered_by": __import__("json").dumps({"type": "transport"}),
    }

    trip = asyncio.run(repository.get_trip(_owner(guest_id), trip_id))

    itinerary = trip.trip_state["itinerary_state"]
    assert itinerary["status"] == "ready"
    assert itinerary["current_version"] == {
        "version": 1, "source_guide_revision": 5, "result": {"final_itinerary": {"days": []}},
    }
    assert itinerary["proposed_revision"] == {
        "version": 2, "base_version": 1, "result": {"final_itinerary": {"days": ["revised"]}},
        "affected_days": [1], "changes": ["Day 1: revised"], "triggered_by": {"type": "transport"},
    }


def test_itinerary_state_write_archives_the_active_version_unconditionally():
    """TWM-158: itinerary_versions is no longer written only for the
    superseded version — the active current_version is archived too."""
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)

    asyncio.run(repository.commit_command(
        _owner(guest_id), trip_id, 1, uuid4(), "hash",
        trip_state={
            "status": "free", "stage": "planned", "active_agent": None, "trip_context": {},
            "itinerary_state": {
                "status": "ready",
                "current_version": {"version": 1, "source_guide_revision": 5, "result": {"final_itinerary": {"days": []}}},
                "proposed_revision": None,
            },
        },
        response_trip_state={}, response={"message": None, "agent_meta": None},
        touched_branches=frozenset({"itinerary_state"}),
    ))

    assert (trip_id, 1) in db.itinerary_versions
    assert db.itinerary_state[trip_id] == {"status": "ready", "current_version": 1}
    assert trip_id not in db.itinerary_proposed_revisions


def test_accepting_a_revision_archives_the_outgoing_version_via_new_itinerary_version():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)

    asyncio.run(repository.commit_command(
        _owner(guest_id), trip_id, 1, uuid4(), "hash",
        trip_state={
            "status": "free", "stage": "planned", "active_agent": None, "trip_context": {},
            "itinerary_state": {
                "status": "ready",
                "current_version": {"version": 2, "source_guide_revision": 5, "result": {"final_itinerary": {"days": ["v2"]}}},
                "proposed_revision": None,
            },
        },
        response_trip_state={}, response={"message": None, "agent_meta": None},
        touched_branches=frozenset({"itinerary_state"}),
        new_itinerary_version={"version": 1, "source_guide_revision": 5, "result": {"final_itinerary": {"days": ["v1"]}}},
    ))

    assert (trip_id, 1) in db.itinerary_versions  # outgoing, archived explicitly
    assert (trip_id, 2) in db.itinerary_versions  # new active version, archived unconditionally
    assert db.itinerary_state[trip_id]["current_version"] == 2


def test_get_current_itinerary_reads_the_version_the_pointer_names():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)
    db.itinerary_state[trip_id] = {"status": "ready", "current_version": 2}
    db.itinerary_versions[(trip_id, 1)] = {
        "trip_id": trip_id, "version": 1, "source_guide_revision": 5,
        "result": __import__("json").dumps({"final_itinerary": {"days": ["v1"]}}),
        "created_at": datetime.now(timezone.utc),
    }
    db.itinerary_versions[(trip_id, 2)] = {
        "trip_id": trip_id, "version": 2, "source_guide_revision": 6,
        "result": __import__("json").dumps({"final_itinerary": {"days": ["v2"]}}),
        "created_at": datetime.now(timezone.utc),
    }

    current = asyncio.run(repository.get_current_itinerary(_owner(guest_id), trip_id))

    assert current.version == 2
    assert current.source_guide_revision == 6
    assert current.result == {"final_itinerary": {"days": ["v2"]}}


def test_get_current_itinerary_none_before_any_itinerary_generated():
    db = FakeDatabase(SCHEMA)
    repository = _repository(db)
    guest_id = uuid4()
    trip_id = _seed_trip(db, guest_id)

    assert asyncio.run(repository.get_current_itinerary(_owner(guest_id), trip_id)) is None
