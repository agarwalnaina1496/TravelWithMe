"""Deterministic, narrowly-scoped fixes for known LLM output shape slips.

Each fix targets exactly one observed failure pattern -- a specific field
that has actually been seen in the wrong place -- and moves it to where the
output schema expects it, before schema validation ever runs. This is not a
generic reshaping/guessing layer: a fix only ever moves a well-identified
field between two well-identified locations, and only when the destination
doesn't already hold a value (never silently overwrites real data). Applying
one turns a validation failure that would otherwise cost a retry (or fail
outright) into a first-attempt success.

Add a new agent's fixes only once a real failure has been observed for it --
this module intentionally excludes agents with no evidenced pattern yet.
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


def _fix_meridian_last_meridian_message_nesting(decoded: Any) -> bool:
    """TWM-234: observed live -- Meridian nested `last_meridian_message` as
    a sibling of `conversation_context` on `matcher_state`, instead of
    inside it. `MeridianAgentOutput`'s validator only ever reads the field
    from inside `conversation_context`, so the misplacement always failed
    the contract. Moves it in only when the nested slot is still empty;
    never overwrites an already-set nested value."""

    if not isinstance(decoded, dict):
        return False
    state_delta = decoded.get("state_delta")
    if not isinstance(state_delta, dict):
        return False
    matcher_state = state_delta.get("matcher_state")
    if not isinstance(matcher_state, dict):
        return False
    stray = matcher_state.get("last_meridian_message")
    if not stray:
        return False
    context = matcher_state.get("conversation_context")
    if not isinstance(context, dict):
        return False
    moved = False
    if not context.get("last_meridian_message"):
        context["last_meridian_message"] = stray
        moved = True
    del matcher_state["last_meridian_message"]
    return moved


# Scout is deliberately excluded -- no evidenced shape-slip pattern for it,
# and its output shape (message / free-form trip_context / intent) has
# nothing resembling this nesting ambiguity to begin with.
_SHAPE_FIXES: dict[AgentName, tuple[ShapeFix, ...]] = {
    "meridian": (
        ("last_meridian_message_nesting", _fix_meridian_last_meridian_message_nesting),
    ),
}
