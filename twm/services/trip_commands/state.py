"""TripState shaping and merge helpers shared across command handlers."""

import copy
from typing import Any

from ...persistence.contracts import TripRecord
from ...schemas.trips import TripResponse


def trip_response(record: TripRecord) -> TripResponse:
    return TripResponse.model_validate(record, from_attributes=True)


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
        "advisor_state": {"conversation_context": {}, "artifacts": []},
        "matcher_state": {"conversation_context": {}, "recommendations": []},
        "planner_state": {},
        "itinerary_state": {},
    }
    for name, default in object_branches.items():
        if not isinstance(state.get(name), dict):
            state[name] = copy.deepcopy(default)
    return state
