"""TWM-223: the command dispatch registry and its handlers' preconditions.

Covers the registry-completeness invariant plus each precondition guard in
isolation. The end-to-end command behaviour is regression-covered
byte-identical by tests/api/apitest_trips.py and the per-command unit tests.
"""

from uuid import uuid4

import pytest

from twm.schemas.trips import TripCommandName, TripCommandRequest
from twm.services.trip_commands.errors import InvalidTripCommandError
from twm.services.trip_commands.handlers import (
    COMMAND_HANDLERS,
    CommandContext,
    ReopenDestinationFreshHandler,
    ReopenDestinationRevisitHandler,
    StartPlanningHandler,
    _has_planning_destination,
)
from twm.services.trip_commands.state import canonical_state


def _ctx(state, command="continue", **fields):
    payload = TripCommandRequest(
        command=command, expected_version=1, idempotency_key=uuid4(), **fields
    )
    return CommandContext(
        state=state, payload=payload, engine=None, logger=None, latest_recommendation=None
    )


def test_every_command_name_has_exactly_one_handler():
    names = set(TripCommandName.__args__)
    assert set(COMMAND_HANDLERS) == names, (
        "COMMAND_HANDLERS must map every TripCommandName and nothing else — "
        f"missing {names - set(COMMAND_HANDLERS)}, extra {set(COMMAND_HANDLERS) - names}"
    )


def test_only_the_booking_setup_commands_are_post_freeze_ok():
    post_freeze = {name for name, h in COMMAND_HANDLERS.items() if h.post_freeze_ok}
    assert post_freeze == {
        "start_itinerary",
        "set_party",
        "set_search_pref",
        "clear_search_pref",
    }


class TestStartPlanningPrecondition:
    def test_rejects_without_a_destination(self):
        state = canonical_state({"stage": "new"})
        with pytest.raises(InvalidTripCommandError, match="destination"):
            StartPlanningHandler().precondition(_ctx(state, command="start_planning"))

    def test_rejects_from_a_stage_other_than_new_or_matched(self):
        state = canonical_state(
            {"stage": "recommended", "trip_context": {"destinations": ["Goa"]}}
        )
        with pytest.raises(InvalidTripCommandError, match="new or matched"):
            StartPlanningHandler().precondition(_ctx(state, command="start_planning"))

    def test_allows_new_stage_with_a_destination(self):
        state = canonical_state(
            {"stage": "new", "trip_context": {"destinations": ["Goa"]}}
        )
        StartPlanningHandler().precondition(_ctx(state, command="start_planning"))

    def test_has_planning_destination_needs_a_non_blank_string_entry(self):
        assert _has_planning_destination({"destinations": ["Goa"]}) is True
        assert _has_planning_destination({"destinations": []}) is False
        assert _has_planning_destination({"destinations": ["  "]}) is False
        assert _has_planning_destination({}) is False


@pytest.mark.parametrize(
    "handler_cls, command",
    [
        (ReopenDestinationRevisitHandler, "reopen_destination_revisit"),
        (ReopenDestinationFreshHandler, "reopen_destination_fresh"),
    ],
)
class TestReopenDestinationPrecondition:
    def test_rejects_with_no_pending_choice(self, handler_cls, command):
        state = canonical_state({"stage": "planning"})
        with pytest.raises(InvalidTripCommandError, match="pending destination-reopen"):
            handler_cls().precondition(_ctx(state, command=command))

    def test_allows_when_a_reopen_choice_is_pending(self, handler_cls, command):
        state = canonical_state(
            {
                "stage": "planning",
                "planner_state": {
                    "conversation_context": {"awaiting": "destination_reopen_choice"}
                },
            }
        )
        handler_cls().precondition(_ctx(state, command=command))
