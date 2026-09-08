"""The sub-state branches large/variable enough to live in their own tables
and be shaped in/out of a command response individually.

This is the one place both the service layer (``trip_commands.state``) and
the persistence layer (``PostgresTripRepository``) agree on which branches
exist and what "empty" looks like — a bottom-layer fact, so persistence no
longer has to reach up into ``services`` for it (TWM-223 / TWM-225).
"""

from __future__ import annotations

import copy
from typing import Any

# matcher/planner/itinerary/booking_setup. advisor_state is deliberately
# excluded — it never appears in a command response (see
# shape_command_trip_state) and is not a dedicated branch table.
TOUCHABLE_BRANCHES: tuple[str, ...] = (
    "matcher_state",
    "planner_state",
    "itinerary_state",
    "booking_setup",
)

# The canonical "nothing here yet" shape of each touchable branch — kept in
# sync with canonical_state()'s object_branches (which imports this).
_EMPTY_BRANCH: dict[str, Any] = {
    "matcher_state": {"conversation_context": {}},
    "planner_state": {},
    "itinerary_state": {},
    "booking_setup": {},
}


def empty_branch(name: str) -> Any:
    """A fresh copy of the canonical-empty value for one touchable branch."""
    return copy.deepcopy(_EMPTY_BRANCH[name])


def populated_touchable_branches(trip_state: dict[str, Any]) -> frozenset[str]:
    """Which touchable branches of a full ``trip_state`` carry real content —
    i.e. differ from their canonical-empty shape. This is what a full-state
    write (create / replace) needs in order to know which branch tables to
    populate; it replaces the old ``touched_branches(state, canonical_state({}))``
    idiom for that case, without importing the service layer.
    """
    return frozenset(
        name
        for name in TOUCHABLE_BRANCHES
        if trip_state.get(name, _EMPTY_BRANCH[name]) != _EMPTY_BRANCH[name]
    )
