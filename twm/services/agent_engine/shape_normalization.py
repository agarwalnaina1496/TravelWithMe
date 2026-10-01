"""Deterministic, pre-validation fixes for known-shape LLM output slips.

Each fix targets exactly one named field and relocates it from exactly one
named wrong location to exactly one named correct location, before schema
validation ever runs -- normalize, then validate, reject only if it's still
invalid after that. Three hard rules, independent of how likely or how
severe any given mistake is -- correctness rules, not risk judgment calls:

1. Never overwrite or discard a real value to make room for another. A
   scalar field present in both the right place and the wrong place is left
   completely untouched in both places -- there is no principled way to
   know which of two conflicting values is correct, so this module doesn't
   guess; it leaves the output exactly as the agent produced it and lets
   schema validation (then the retry) handle it from there. A list-valued
   field present in both places is concatenated instead, since that loses
   nothing from either side.
2. Only ever relocate a field whose correct destination is uniquely
   determined by the shape alone. If the correct destination could be one
   of several sibling containers (which of N timeline items a stray `hubs`
   list belongs to, for instance), that is not something this module
   decides -- doing so would mean guessing, which is a correctness problem,
   not a question of how "ambiguous" the mistake looks.
3. A fix only ever moves or merges a value already present in the output --
   it never invents, infers, or fabricates one.

This runs by default for Meridian, Guide, and Atlas -- every field in their
schemas with a uniquely-determined parent gets a fix here, independent of
whether a failure has actually been observed for it yet. Scout is
deliberately excluded: its output shape (message / free-form trip_context /
intent) has no nested field to relocate in the first place.
"""

from typing import Any, Callable

from .contracts import AgentName

# Each entry names the fix for logging/telemetry; `apply` mutates `decoded`
# in place and returns True only when it actually changed something.
ShapeFix = tuple[str, Callable[[Any], bool]]


def normalize_agent_output(agent: AgentName, decoded: Any) -> list[str]:
    """Apply every registered fix for `agent` to `decoded` in place. Returns
    the names of the fixes that actually changed something, in order."""

    applied: list[str] = []
    for name, apply in _SHAPE_FIXES.get(agent, ()):
        if apply(decoded):
            applied.append(name)
    return applied


def _relocate_sibling_field(
    decoded: Any, *, outer_path: list[str], inner_container: str, field: str
) -> bool:
    """Generic engine behind every scalar fix below: if `field` sits
    directly on the dict at `outer_path` (a sibling of `inner_container`)
    instead of inside `decoded[*outer_path][inner_container]`, move it in --
    but only when that inner slot is genuinely empty. If a value already
    sits at the correct location too, this is a conflict between two values
    the agent produced in the same breath with no principled way to pick a
    winner -- so it leaves both exactly as given rather than discarding
    either one, and schema validation (then the retry) takes it from there."""

    container = decoded
    for key in outer_path:
        if not isinstance(container, dict):
            return False
        container = container.get(key)
    if not isinstance(container, dict):
        return False
    stray = container.get(field)
    if not stray:
        return False
    inner = container.get(inner_container)
    if inner is not None and not isinstance(inner, dict):
        return False
    if inner is not None and inner.get(field):
        return False  # conflict -- leave both values untouched, don't guess
    # The inner container can be legitimately absent (e.g. Guide's optional
    # conversation_context) precisely when the only field it would have
    # held was misplaced here instead -- create it rather than bailing out.
    if inner is None:
        inner = {}
        container[inner_container] = inner
    inner[field] = stray
    del container[field]
    return True


def _fix_meridian_last_meridian_message_nesting(decoded: Any) -> bool:
    """TWM-234: observed live -- Meridian nested `last_meridian_message` as
    a sibling of `conversation_context` on `matcher_state`, instead of
    inside it. `MeridianAgentOutput`'s validator only ever reads the field
    from inside `conversation_context`, so the misplacement always failed
    the contract."""

    return _relocate_sibling_field(
        decoded,
        outer_path=["state_delta", "matcher_state"],
        inner_container="conversation_context",
        field="last_meridian_message",
    )


def _fix_guide_awaiting_nesting(decoded: Any) -> bool:
    """TWM-234: `awaiting`'s correct parent is uniquely determined --
    `state_delta.planner_state.conversation_context.awaiting`, never a
    sibling of `conversation_context` directly on `planner_state`. Runs by
    default regardless of whether this exact mistake has been observed for
    Guide yet; the fix doesn't wait for a failure to justify itself, only
    for the destination to be unambiguous, which it is here."""

    return _relocate_sibling_field(
        decoded,
        outer_path=["state_delta", "planner_state"],
        inner_container="conversation_context",
        field="awaiting",
    )


def _fix_atlas_assumptions_nesting(decoded: Any) -> bool:
    """TWM-234: `assumptions` belongs on `final_itinerary` directly, never
    inside `trip_summary` -- the two sit at the same nesting depth as
    siblings on `final_itinerary`, exactly the kind of flattenable mistake
    this module exists to catch. (Atlas's other nesting risks -- `hubs` and
    `stay_price_estimate` living under the wrong one of several sibling
    timeline items/days -- are deliberately left unfixed: relocating those
    would mean guessing which item a stray value belongs to, which this
    module never does.)

    Unlike the scalar fixes above, a real value already present at the
    correct location isn't a reason to leave the stray one untouched:
    `assumptions` is a list, and lists concatenate without discarding
    anything from either side -- that's a merge, not a guess."""

    final_itinerary = decoded.get("final_itinerary") if isinstance(decoded, dict) else None
    if not isinstance(final_itinerary, dict):
        return False
    trip_summary = final_itinerary.get("trip_summary")
    if not isinstance(trip_summary, dict):
        return False
    stray = trip_summary.get("assumptions")
    if not stray:
        return False
    if not isinstance(stray, list):
        return False  # malformed shape; leave it for validation to reject
    existing = final_itinerary.get("assumptions")
    if existing:
        if not isinstance(existing, list):
            return False  # malformed shape; leave it for validation to reject
        final_itinerary["assumptions"] = existing + stray
    else:
        final_itinerary["assumptions"] = stray
    del trip_summary["assumptions"]
    return True


# Scout is deliberately excluded -- no evidenced shape-slip pattern for it,
# and its output shape (message / free-form trip_context / intent) has
# nothing resembling this nesting ambiguity to begin with.
_SHAPE_FIXES: dict[AgentName, tuple[ShapeFix, ...]] = {
    "meridian": (
        ("last_meridian_message_nesting", _fix_meridian_last_meridian_message_nesting),
    ),
    "guide": (
        ("awaiting_nesting", _fix_guide_awaiting_nesting),
    ),
    "atlas": (
        ("assumptions_nesting", _fix_atlas_assumptions_nesting),
    ),
}
