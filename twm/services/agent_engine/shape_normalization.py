"""Default, pre-validation normalization of every agent's raw LLM output.

This is not a fix applied to a broken response -- it is a standard shape
pass every decoded Meridian/Guide/Atlas response goes through before schema
validation, the same way any boundary normalizes an external payload before
trusting its shape. An LLM's own JSON nesting is not guaranteed to exactly
match the schema on every generation even when every fact in it is correct,
so normalization runs unconditionally, on every response, not only after a
validation failure is observed.

Two things have to stay balanced here, and neither wins outright:

1. Don't normalize so aggressively that it risks losing or corrupting data.
   A scalar field present at both a correct and an incorrect location is
   left completely untouched at both -- there is no principled way to know
   which of two conflicting values the agent meant, so this module never
   guesses; it leaves the output exactly as given and lets schema validation
   (then the retry) handle it. A list-valued field present in both places is
   concatenated instead, since that loses nothing from either side. A
   normalization only ever relocates a field whose correct destination is
   uniquely determined by the shape alone -- never guessing across several
   sibling containers, such as which of N timeline items a stray value
   belongs to.
2. Don't withhold normalization just to avoid that risk either. Every field
   in Meridian's, Guide's, and Atlas's schemas with a uniquely-determined
   parent gets a normalization rule here, independent of whether a slip has
   actually been observed for it yet -- this runs by default, not reactively
   after a failure.
3. A normalization only ever moves or merges a value already present in the
   output -- it never invents, infers, or fabricates one.

Scout is deliberately excluded: its output shape (message / free-form
trip_context / intent) has no nested field to relocate in the first place.
"""

from typing import Any, Callable

from .contracts import AgentName

# Each entry names the normalization for logging/telemetry; `apply` mutates
# `decoded` in place and returns True only when it actually changed something.
NormalizationRule = tuple[str, Callable[[Any], bool]]


def normalize_agent_output(agent: AgentName, decoded: Any) -> list[str]:
    """Apply every registered normalization for `agent` to `decoded` in
    place. Returns the names of the normalizations that actually changed
    something, in order."""

    applied: list[str] = []
    for name, apply in _NORMALIZATION_RULES.get(agent, ()):
        if apply(decoded):
            applied.append(name)
    return applied


def _relocate_sibling_field(
    decoded: Any, *, outer_path: list[str], inner_container: str, field: str
) -> bool:
    """Generic engine behind every scalar normalization below: if `field`
    sits directly on the dict at `outer_path` (a sibling of
    `inner_container`) instead of inside `decoded[*outer_path][inner_container]`,
    move it in -- but only when that inner slot is genuinely empty. If a
    value already sits at the correct location too, this is a conflict
    between two values the agent produced in the same breath with no
    principled way to pick a winner -- so it leaves both exactly as given
    rather than discarding either one, and schema validation (then the
    retry) takes it from there."""

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


def _normalize_meridian_conversation_context(decoded: Any) -> bool:
    """TWM-234: `last_meridian_message`'s correct, uniquely-determined
    parent is `state_delta.matcher_state.conversation_context` --
    `MeridianAgentOutput`'s validator only ever reads the field from inside
    `conversation_context`, never as a sibling of it directly on
    `matcher_state`."""

    return _relocate_sibling_field(
        decoded,
        outer_path=["state_delta", "matcher_state"],
        inner_container="conversation_context",
        field="last_meridian_message",
    )


def _normalize_guide_conversation_context(decoded: Any) -> bool:
    """TWM-234: `awaiting`'s correct, uniquely-determined parent is
    `state_delta.planner_state.conversation_context.awaiting`, never a
    sibling of `conversation_context` directly on `planner_state`. Runs by
    default regardless of whether this exact shape slip has been observed
    for Guide yet; normalization doesn't wait for a failure to justify
    itself, only for the destination to be unambiguous, which it is here."""

    return _relocate_sibling_field(
        decoded,
        outer_path=["state_delta", "planner_state"],
        inner_container="conversation_context",
        field="awaiting",
    )


def _normalize_atlas_assumptions(decoded: Any) -> bool:
    """TWM-234: `assumptions`'s correct, uniquely-determined parent is
    `final_itinerary` directly, never inside `trip_summary` -- the two sit
    at the same nesting depth as siblings on `final_itinerary`, exactly the
    kind of flattenable shape this module normalizes by default. (Atlas's
    other nesting risks -- `hubs` and `stay_price_estimate` living under the
    wrong one of several sibling timeline items/days -- are deliberately
    left alone: relocating those would mean guessing which item a stray
    value belongs to, which this module never does.)

    Unlike the scalar normalizations above, a real value already present at
    the correct location isn't a reason to leave the stray one untouched:
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


# Scout is deliberately excluded -- its output shape (message / free-form
# trip_context / intent) has nothing resembling this nesting ambiguity to
# begin with.
_NORMALIZATION_RULES: dict[AgentName, tuple[NormalizationRule, ...]] = {
    "meridian": (
        ("meridian_conversation_context", _normalize_meridian_conversation_context),
    ),
    "guide": (
        ("guide_conversation_context", _normalize_guide_conversation_context),
    ),
    "atlas": (
        ("atlas_assumptions", _normalize_atlas_assumptions),
    ),
}
