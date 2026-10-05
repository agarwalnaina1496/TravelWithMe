"""Shared building blocks for every agent's output contract.

An agent's output model does three jobs, and this module keeps them apart:

1. **The LLM contract** -- the fields the model genuinely decides. This is the
   JSON Schema shown to the model, so anything Backend can compute itself
   (a rank number, a derived ``trip_type``, an echo of the request) is hidden
   from it with ``SkipJsonSchema`` and filled in deterministically.
2. **Boundary tolerance** -- lossless healing of harmless slips (an empty list
   where the field should be absent, a repeated item, tiers in the wrong
   order, an unknown key). Declared next to the field it heals, so a contract
   and its tolerance cannot drift apart. Every heal that fires is recorded on
   the validation context and surfaces as ``be.agent.output.normalized``.
3. **Rejection** -- reserved for what a downstream consumer or the trust
   boundary genuinely depends on. A rule that only checks that the model
   echoed or derived something correctly belongs in (1), not here.

Healing never invents a value: it relocates, merges, de-duplicates, orders,
drops an empty or unknown value, or lowers a claim the output cannot support.
"""

from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    ValidationInfo,
    model_validator,
)

# Validation-context key under which the engine collects the names of the
# tolerances that fired for one response.
HEALED_KEY = "healed"

# The one non-empty trimmed string every agent contract uses.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def record_heal(info: ValidationInfo, name: str) -> None:
    """Note on the validation context that a tolerance fired."""

    sink = (info.context or {}).get(HEALED_KEY)
    if sink is not None and name not in sink:
        sink.append(name)


class AgentContent(BaseModel):
    """Base for every model an LLM fills in.

    Unknown keys are dropped (and recorded) instead of rejecting the whole
    response -- the schema is a whitelist, so a dropped key can never reach
    persisted state -- while the schema shown to the model still says
    ``additionalProperties: false``. The trust-critical ``state_delta``
    branches do *not* extend this: an agent reaching for a Backend-owned key
    there is a signal worth rejecting loudly, not a slip to smooth over.
    """

    model_config = ConfigDict(extra="ignore", json_schema_extra={"additionalProperties": False})

    @model_validator(mode="before")
    @classmethod
    def _drop_unknown_keys(cls, data: Any, info: ValidationInfo) -> Any:
        if isinstance(data, dict):
            unknown = data.keys() - cls.model_fields.keys()
            if unknown:
                record_heal(info, f"{cls.__name__}.dropped_unknown_keys")
        return data


def _empty_as_none(value: Any, info: ValidationInfo) -> Any:
    if isinstance(value, (list, dict, str)) and not value:
        record_heal(info, f"{info.field_name}.empty_as_absent")
        return None
    return value


# ``[]`` / ``{}`` / ``""`` for an optional field means "nothing to say" -- the
# same as leaving it out. Use as ``Annotated[Optional[...], EmptyAsNone]``.
EmptyAsNone = BeforeValidator(_empty_as_none)


def dedupe_casefold(values: list[Any]) -> list[Any]:
    """First occurrence wins; strings compare case-insensitively."""

    seen: set[Any] = set()
    kept: list[Any] = []
    for value in values:
        key = value.casefold() if isinstance(value, str) else value
        if key not in seen:
            seen.add(key)
            kept.append(value)
    return kept


def _deduped(value: Any, info: ValidationInfo) -> Any:
    if not isinstance(value, list):
        return value
    kept = dedupe_casefold(value)
    if len(kept) != len(value):
        record_heal(info, f"{info.field_name}.deduplicated")
    return kept


# A list that must not repeat an item: repeats are folded into the first.
Deduped = BeforeValidator(_deduped)


def ensure_unique(values: list[str], what: str) -> None:
    """Reject a repeat in a list whose items are identities, not content."""

    normalized = [value.casefold() for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{what} must be unique")


def ensure_ordered_range(low: Optional[float], high: Optional[float], what: str) -> None:
    """Reject a range whose high end sits below its low end."""

    if low is not None and high is not None and high < low:
        raise ValueError(f"{what} maximum must be at least its minimum")


def relocate_into(
    data: Any, *, container: str, field: str, info: ValidationInfo
) -> Any:
    """Move ``field`` from sibling-of-``container`` into ``container``.

    Only when the destination is uniquely determined and empty; if the
    container already holds a value there is no principled winner, so both
    are left as given and validation takes it from there.
    """

    if not isinstance(data, dict) or not data.get(field):
        return data
    inner = data.get(container)
    if inner is not None and not isinstance(inner, dict):
        return data
    if inner is not None and inner.get(field):
        return data
    data = {**data}
    data[container] = {**(inner or {}), field: data.pop(field)}
    record_heal(info, f"{field}.relocated_into_{container}")
    return data
