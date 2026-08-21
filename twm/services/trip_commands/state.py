"""TripState shaping and merge helpers shared across command handlers."""

import copy
from typing import Any, get_args

from ...schemas.scout import TripStage
from ...telemetry import TelemetryLogger
from .errors import InvalidTripCommandError

# The canonical stage set (TWM-188) — `TripStage` is the single source of
# truth for valid values; this frozenset exists only for O(1) membership
# checks in `set_stage`.
VALID_STAGES: frozenset[str] = frozenset(get_args(TripStage))

# STAGE_TRANSITIONS documents the from-stage -> {legal to-stage} graph.
# Enforced incrementally via ENFORCED_FROM_STAGES below, one stage at a
# time (TWM-188) rather than flipped on globally — a stage still carrying
# an inaccurate edge would make enforcement reject a flow that's supposed
# to work, or silently legalize a bug. Update this table in the same change
# as any fix that adds, removes, or corrects an edge.
#
# start_planning (service.py) and select_destination (matcher_commands.py)
# both used to lack a current-stage precondition entirely — "planning" and
# "matched" were technically reachable from anywhere. Both gaps are closed
# now (start_planning: new/matched only; select_destination: caught
# indirectly once "matched" became an enforced from-stage, since
# "matched" -> "matched" isn't a legal edge).
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
    "recommended": frozenset({
        "matched",   # select_destination
        "matching",  # more_like_this / refinement-triggered traveler_message,
        # transiently, while Meridian reprocesses (TWM-188) — apply_meridian
        # flips it back to "recommended" once it responds.
    }),
    "matched": frozenset({
        "planning",  # start_planning, once a destination is set
        "matching",  # a matched trip's traveler_message/continue with no
        # genuine ambiguity to classify (the destination is already
        # chosen, planning hasn't started) — service.py's
        # _reopen_matching_from_matched routes straight back to Meridian,
        # clearing the obsolete selected_option deterministically.
    }),
    "planning": frozenset({
        "plan_ready",  # apply_guide's revision turn first produces (or
        # re-produces, after a revision) a non-empty day_plan (TWM-188
        # item 8) — mirrors "matching" -> "recommended" on the Discover side.
        "matching",  # _reopen_destination_discovery when latest_recommendation
        # is None (known-destination-direct, Meridian never ran — only
        # sensible target), or the traveler's explicit "start fresh" choice
        # (reopen_destination_fresh) when one did exist.
        "recommended",  # the traveler's explicit "revisit existing list"
        # choice (reopen_destination_revisit) when latest_recommendation is
        # not None — TWM-188 item 3, now wired.
        # "planned" is no longer a direct edge from here — approve_plan
        # requires a non-empty day_plan (_validate_guide_event), which now
        # always means stage is already "plan_ready" by the time it fires.
    }),
    "plan_ready": frozenset({
        "planning",  # a revision request on an existing plan, transiently,
        # while Guide reprocesses (TWM-188 item 8) — mirrors
        # "recommended" -> "matching"; day_plan itself is never cleared.
        "planned",   # approve_plan -> _apply_plan_freeze
    }),
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


# Stages whose STAGE_TRANSITIONS edges are now fully accurate (no-ops
# removed, missing edges added) and safe to enforce. Grown one stage at a
# time (TWM-188) rather than flipped on globally, since a stage still
# carrying a known gap would make enforcement reject a flow that's supposed
# to work. "new" was the first stage enforced. "matching" is the second:
# its only outgoing write site (apply_meridian's success path, to
# "recommended") already agreed with the table — added alongside the
# "recommended" -> "matching" refinement fix (more_like_this / the
# refinement-triggered traveler_message path) since both land together.
# "recommended" and "matched" are the third and fourth: "recommended"'s
# outgoing write sites (select_destination -> "matched", more_like_this/
# refinement -> "matching") already agreed with the table. "matched"
# needed one table correction first — a matched trip's traveler_message/
# continue routes straight back to Meridian (matched -> matching, clearing
# the obsolete selection via service.py's _reopen_matching_from_matched);
# this is a real, tested flow, not a bug, so it's now a documented edge
# rather than an omission. As a side effect,
# enforcing "matched" also closes select_destination's own missing
# current-stage precondition for the one case an enforced from-stage can
# now catch: a stray re-selection while a trip is already "matched" is
# rejected, since "matched" -> "matched" isn't a legal edge.
#
# "planning", "plan_ready", and "planned" are the fifth, sixth, and
# seventh: needed apply_guide to actually start writing "plan_ready"
# (TWM-188 item 8) before "planning"'s own edges were accurate enough to
# enforce — day_plan becoming non-empty now always flips stage there, and
# a revision request on an already-ready plan transiently flips back to
# "planning" first. _reopen_destination_discovery's history-aware
# "recommended" branch (item 3) is still deferred — "planning" only ever
# targets "matching" for that flow today, which is still what the table
# documents, so enforcing "planning" doesn't require that fix first.
# "plan_ready"'s and "planned"'s own outgoing write sites already agreed
# with the table (approve_plan always runs with day_plan non-empty, which
# now always means stage is already "plan_ready"; nothing writes stage
# post-freeze), so both were safe to add alongside "planning".
ENFORCED_FROM_STAGES: frozenset[str] = frozenset({
    "new", "matching", "recommended", "matched",
    "planning", "plan_ready", "planned",
})


def set_stage(
    state: dict[str, Any],
    new_stage: str,
    logger: TelemetryLogger | None = None,
    context: str | None = None,
) -> None:
    """Write `stage`, validated against the canonical stage set.

    Every `state["stage"] = ...` write site in trip_commands routes through
    here (TWM-188) so a typo or stray string can never silently persist.
    Full from-stage/to-stage transition-graph enforcement is rolled out one
    stage at a time via `ENFORCED_FROM_STAGES` — see its comment — rather
    than all at once, since several stages still carry documented gaps that
    would make a global switch reject flows that are supposed to work.
    """
    if new_stage not in VALID_STAGES:
        raise InvalidTripCommandError(f"{new_stage!r} is not a valid trip stage.")
    current_stage = state.get("stage")
    if current_stage in ENFORCED_FROM_STAGES:
        allowed = STAGE_TRANSITIONS.get(current_stage, frozenset())
        if new_stage not in allowed:
            if logger is not None:
                logger.warning(
                    "Rejected an illegal trip stage transition.",
                    event="be.trip.stage.transition_rejected",
                    source="application",
                    trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
                    from_stage=current_stage,
                    to_stage=new_stage,
                    context=context,
                )
            raise InvalidTripCommandError(
                f"Illegal trip stage transition: {current_stage!r} -> {new_stage!r}."
            )
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
