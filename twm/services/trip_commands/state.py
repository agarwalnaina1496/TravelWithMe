"""TripState shaping and merge helpers shared across command handlers."""

import copy
from typing import Any


def deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            deep_merge(current, value)
        else:
            target[key] = copy.deepcopy(value)


def merge_operational_state(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key == "rejected_options" and isinstance(value, list):
            current = target.setdefault(key, [])
            for item in value:
                if item not in current:
                    current.append(copy.deepcopy(item))
        elif isinstance(value, dict):
            child = target.setdefault(key, {})
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            merge_operational_state(child, value)
        else:
            target[key] = copy.deepcopy(value)


def canonical_state(value: dict[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(value)
    state.setdefault("status", "free")
    state.setdefault("stage", "new")
    state.setdefault("active_agent", "scout")
    object_branches = {
        "trip_context": {},
        # advisor_state carries only read-back handoff context (Scout/
        # Meridian's own prompts read conversation_context) — no artifacts
        # log; nothing reads it back and it grew unbounded for no reason.
        "advisor_state": {"conversation_context": {}},
        "matcher_state": {"conversation_context": {}, "recommendations": []},
        "planner_state": {},
        "itinerary_state": {},
        "logistics_state": {"anchors": []},
    }
    for name, default in object_branches.items():
        if not isinstance(state.get(name), dict):
            state[name] = copy.deepcopy(default)
    return state


# Sub-state branches large/variable enough to matter for command-response
# size; response shaping includes a branch only when a command actually
# touched it. advisor_state is deliberately excluded from this set — it
# never appears in a command response at all (see shape_command_trip_state).
TOUCHABLE_BRANCHES = ("matcher_state", "planner_state", "itinerary_state", "logistics_state")

# Always-present fields a command response needs regardless of what a
# command touched — everything resume/CTA logic and the next command's
# routing decision (service.py's stage/active_agent dispatch) depends on.
_CORE_FIELDS = ("trip_id", "status", "stage", "active_agent", "trip_context")


def snapshot_touchable_branches(state: dict[str, Any]) -> dict[str, Any]:
    """Deep copy of the touchable branches, taken before a command runs."""
    return {key: copy.deepcopy(state[key]) for key in TOUCHABLE_BRANCHES}


def touched_branches(state: dict[str, Any], before: dict[str, Any]) -> set[str]:
    return {key for key in TOUCHABLE_BRANCHES if state[key] != before[key]}


def shape_command_trip_state(state: dict[str, Any], touched: set[str]) -> dict[str, Any]:
    """Core fields always; touchable branches only when this command touched them."""
    shaped = {field: state[field] for field in _CORE_FIELDS}
    for key in TOUCHABLE_BRANCHES:
        if key in touched:
            shaped[key] = state[key]
    return shaped
