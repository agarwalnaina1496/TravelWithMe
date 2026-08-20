"""TripState shaping and merge helpers shared across command handlers."""

import copy
from typing import Any, get_args

from ...schemas.scout import ScoutStage
from .errors import InvalidTripCommandError

# The canonical stage set (TWM-188) — `ScoutStage` is the single source of
# truth for valid values; this frozenset exists only for O(1) membership
# checks in `set_stage`.
VALID_STAGES: frozenset[str] = frozenset(get_args(ScoutStage))

# STAGE_TRANSITIONS documents the from-stage -> {legal to-stage} graph as it
# exists in code TODAY (TWM-188) — it is NOT enforced anywhere yet. Today's
# write sites don't yet agree on a consistent graph (see the inline notes
# below), so gating writes against this table now would either encode a
# known bug as "legal" or break a flow that still relies on it. This table
# exists purely as a single documented reference; update it in the same
# change as any fix that adds, removes, or corrects an edge, and wire it
# into `set_stage` as an enforced check once every edge below is accurate.
#
# Two unguarded write sites affect nearly every "to: planning"/"to: matched"
# entry below, not just one from-stage, so they're called out once here
# rather than repeated per entry:
#   - start_planning (service.py) checks only that a destination exists in
#     trip_context — no current-stage precondition — so "planning" is
#     technically reachable from ANY non-terminal stage today, not just
#     the "matched" edge documented below.
#   - select_destination (matcher_commands.py) checks only that
#     latest_recommendation exists — no current-stage precondition — so
#     "matched" is technically reachable from any stage where a
#     recommendation row is still fetchable, not just from "recommended".
# Both are confirmed gaps to close once enforcement is scoped, not
# intentional design.
STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({
        "matching",   # discover_entry, or Scout handing off with matcher intent
        "planning",   # known_destination_entry/start_planning, or Scout planner intent
    }),
    "matching": frozenset({
        "recommended",  # apply_meridian succeeds with a candidate list
        # NEEDS_CLARIFICATION performs no stage write (removed, TWM-188) —
        # stage is already "matching" whenever apply_meridian runs, so
        # there is no self-loop here to document.
    }),
    "recommendation_ready": frozenset(),  # dead value, no write site produces it; slated for removal (TWM-188 item 2)
    "recommended": frozenset({
        "matched",  # select_destination
        # "matching" (refinement via more_like_this / the refinement
        # drawer) is a confirmed GAP, not yet wired — those paths currently
        # leave stage at "recommended" instead of flipping back (TWM-188).
    }),
    "matched": frozenset({
        "planning",  # start_planning, once a destination is set
    }),
    "planning": frozenset({
        # Every Guide revision turn performs no stage write (removed,
        # TWM-188) — stage is already "planning" whenever apply_guide's
        # non-freeze path runs, so there is no self-loop here to document.
        "matching",  # _reopen_destination_discovery — unconditional today,
        # regardless of trip history.
        # "recommended" (revisit an existing recommendation list when
        # latest_recommendation is not None, traveler chooses "revisit"
        # over "fresh discovery") is a confirmed GAP, not yet wired —
        # _reopen_destination_discovery always targets "matching" today,
        # never branching into "recommended" (TWM-188).
        "planned",   # approve_plan -> _apply_plan_freeze
    }),
    # Reserved — no write site produces "plan_ready" yet (TWM-188 item 8).
    # Once it lands: "planned" (approve_plan -> _apply_plan_freeze, same as
    # "planning" today) and a transient "planning" (a revision request,
    # mirroring "recommended" -> "matching" — day_plan itself never clears).
    "plan_ready": frozenset(),
    "planned": frozenset(),     # terminal — confirmed no backward transition exists
}


# trip_context fields every specialist (Scout, Meridian, Guide) can extract
# into independently — a later specialist's delta must accumulate onto an
# earlier one's instead of silently dropping it, so these merge as a
# case-insensitive-deduplicated union rather than a plain overwrite.
_TRIP_CONTEXT_UNION_FIELDS = ("preferences", "exclusions")


def merge_trip_context(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key in _TRIP_CONTEXT_UNION_FIELDS and isinstance(value, list):
            current = target.setdefault(key, [])
            if not isinstance(current, list):
                current = []
                target[key] = current
            seen = {item.casefold() for item in current if isinstance(item, str)}
            for item in value:
                if not isinstance(item, str) or item.casefold() in seen:
                    continue
                current.append(item)
                seen.add(item.casefold())
        elif isinstance(value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            merge_trip_context(current, value)
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


def set_stage(state: dict[str, Any], new_stage: str) -> None:
    """Write `stage`, validated against the canonical stage set.

    Every `state["stage"] = ...` write site in trip_commands routes through
    here (TWM-188) so a typo or stray string can never silently persist.
    This intentionally validates the VALUE only — full from-stage/to-stage
    transition-graph enforcement is deferred until the write sites that
    still disagree on the graph (the `matching`/`planning` self-writes, the
    missing `recommended -> matching` refinement edge, `plan_ready`'s own
    write) are fixed under their own TWM-188 items; enforcing a strict graph
    before then would either encode those bugs as "legal" or break the
    flows that still rely on them.
    """
    if new_stage not in VALID_STAGES:
        raise InvalidTripCommandError(f"{new_stage!r} is not a valid trip stage.")
    state["stage"] = new_stage


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
        # recommendations live in twm_app.matcher_recommendations now
        # (TWM-153) — matcher_state carries only conversation continuity.
        "matcher_state": {"conversation_context": {}},
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
