"""Shared building blocks for every agent's output contract.

An agent's output model does three jobs, and this module keeps them apart:

1. **The LLM contract** -- the fields the model genuinely decides. This is the
   JSON Schema shown to the model (``llm_output_schema``), so anything Backend
   can compute itself (a derived ``trip_type``, an echo of the request) is
   marked ``derived(...)``: left out of what the model sees, filled in
   deterministically, and still part of the published API schema.
2. **Boundary tolerance** -- lossless healing of harmless slips (an empty list
   where the field should be absent, a repeated item, tiers in the wrong
   order, an unknown key, ``"success"`` for ``"SUCCESS"``, ``""`` for ``null``,
   ``1500.5`` for an integer). Declared next to the field it heals, so a
   contract and its tolerance cannot drift apart. Every heal that fires is
   recorded on the validation context and surfaces as
   ``be.agent.output.normalized``.
3. **Rejection** -- reserved for what a downstream consumer or the trust
   boundary genuinely depends on. A rule that only checks that the model
   echoed or derived something correctly belongs in (1), not here.

Healing never invents a value: it relocates, merges, de-duplicates, orders,
re-cases, rounds, drops an empty or unknown value, or lowers a claim the
output cannot support.
"""

import math
import re
from typing import Annotated, Any, Optional, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    model_validator,
)

# Validation-context key under which the engine collects the names of the
# tolerances that fired for one response.
HEALED_KEY = "healed"

# The one non-empty trimmed string every agent contract uses.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# Longest title the trips table stores (varchar(120)).
MAX_TITLE_CHARS = 120

# Marks a field Backend derives. It stays in the model (and in the published
# OpenAPI response schema) but is not part of what the LLM is asked to produce.
DERIVED_KEY = "x-derived"


def derived(**field_options: Any) -> Any:
    """A ``Field`` for a value Backend computes rather than asks the model for."""

    return Field(json_schema_extra={DERIVED_KEY: True}, **field_options)


def llm_output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON Schema shown to the model: the model's schema without its
    derived fields."""

    def strip(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                hidden = [
                    name
                    for name, schema in properties.items()
                    if isinstance(schema, dict) and schema.get(DERIVED_KEY)
                ]
                for name in hidden:
                    del properties[name]
                if isinstance(node.get("required"), list):
                    node["required"] = [n for n in node["required"] if n not in hidden]
            for child in node.values():
                strip(child)
        elif isinstance(node, list):
            for child in node:
                strip(child)

    schema = model.model_json_schema()
    strip(schema)
    return schema


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


# --- absent / empty ---------------------------------------------------------


def _blank(value: Any) -> bool:
    return (isinstance(value, str) and not value.strip()) or (
        isinstance(value, (list, dict)) and not value
    )


def _empty_as_none(value: Any, info: ValidationInfo) -> Any:
    if _blank(value):
        record_heal(info, f"{info.field_name}.empty_as_absent")
        return None
    return value


# ``[]`` / ``{}`` / ``""`` / whitespace for an optional field means "nothing to
# say" -- the same as leaving it out. Use as ``Annotated[Optional[...], EmptyAsNone]``.
EmptyAsNone = BeforeValidator(_empty_as_none)

# An optional piece of text: blank is absent.
OptionalText = Annotated[Optional[Text], EmptyAsNone]


def _null_as_empty_list(value: Any, info: ValidationInfo) -> Any:
    if value is None:
        record_heal(info, f"{info.field_name}.null_as_empty")
        return []
    return value


# ``null`` for a list that defaults to empty is the empty list.
NullAsEmptyList = BeforeValidator(_null_as_empty_list)


def _null_as_empty_object(value: Any, info: ValidationInfo) -> Any:
    if value is None:
        record_heal(info, f"{info.field_name}.null_as_default")
        return {}
    return value


# ``null`` for a nested object that has defaults is that object's defaults.
NullAsDefault = BeforeValidator(_null_as_empty_object)


# --- repeated / ordered -----------------------------------------------------


def dedupe_casefold(values: list[str]) -> list[str]:
    """First occurrence wins; compared case-insensitively and ignoring the
    surrounding whitespace the string type will trim anyway."""

    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        key = value.strip().casefold()
        if key not in seen:
            seen.add(key)
            kept.append(value)
    return kept


def _deduped(value: Any, info: ValidationInfo) -> Any:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return value  # not a list of text: validation reports it
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


def ordered_range(
    low: Optional[float], high: Optional[float], info: ValidationInfo, name: str
) -> tuple[Optional[float], Optional[float]]:
    """A range written high-first is the same range: put it the right way round
    (and say so) instead of failing the response."""

    if low is not None and high is not None and high < low:
        record_heal(info, f"{name}.range_swapped")
        return high, low
    return low, high


def number_by_position(items: Any, field: str) -> tuple[Any, bool]:
    """Number the dicts in ``items`` 1..n in ``field``, honouring the order the
    model meant: when every item already carries an integer, they are ordered
    by it first (stable), so ``[2, 1, 3]`` becomes day 1, 2, 3 in the sequence
    the model numbered them rather than the sequence it happened to list them.

    Returns ``(items, changed)``; anything that is not a list of dicts is
    returned untouched so validation reports it.
    """

    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return items, False
    given = [item.get(field) for item in items]
    ordered = (
        sorted(items, key=lambda item: item[field])
        if all(isinstance(value, int) and not isinstance(value, bool) for value in given)
        else list(items)
    )
    numbered = [{**item, field: number} for number, item in enumerate(ordered, 1)]
    return numbered, numbered != items


# --- enums and numbers ------------------------------------------------------


def slug(text: str) -> str:
    """Case, space and hyphen-insensitive form of a name (``"Mid-range"`` -> ``mid_range``)."""

    return re.sub(r"[\s\-]+", "_", text.strip()).casefold()


def case_insensitive(literal: Any) -> Any:
    """A ``Literal`` that also accepts the model's casing/spacing of a member
    ("success", "Soft Fail", "Mid-range") -- the member itself is never guessed,
    only matched."""

    members = {slug(member): member for member in get_args(literal) if isinstance(member, str)}

    def match(value: Any, info: ValidationInfo) -> Any:
        if isinstance(value, str):
            member = members.get(slug(value))
            if member is not None and member != value:
                record_heal(info, f"{info.field_name}.recased")
                return member
        return value

    return Annotated[literal, BeforeValidator(match)]


_NUMBER_NOISE = re.compile(r"[,\s₹$€£]|\bINR\b|\bRs\.?", re.IGNORECASE)


def _parse_number(value: Any, info: ValidationInfo) -> Any:
    """``"1,500"`` / ``"₹1,500"`` -> ``1500.0``; anything else is left alone."""

    if isinstance(value, str):
        try:
            number = float(_NUMBER_NOISE.sub("", value))
        except ValueError:
            return value
        if math.isfinite(number):
            record_heal(info, f"{info.field_name}.number_parsed")
            return number
    return value


def _lenient_number(value: Any, info: ValidationInfo) -> Any:
    return _parse_number(value, info)


# A numeric field that also accepts a formatted number string.
LenientNumber = BeforeValidator(_lenient_number)


def _lenient_int(value: Any, info: ValidationInfo) -> Any:
    value = _parse_number(value, info)
    if isinstance(value, float) and math.isfinite(value):
        if value != int(value):
            record_heal(info, f"{info.field_name}.rounded")
        return round(value)
    return value


# An integer field (an estimate, a distance) that also accepts "1,500" and a
# fractional 1250.5 -- rounded, since it is an estimate either way.
LenientInt = BeforeValidator(_lenient_int)


def _int_or_none(value: Any, info: ValidationInfo) -> Any:
    if value is None:
        return None
    parsed = _lenient_int(value, info)
    if isinstance(parsed, int) and not isinstance(parsed, bool):
        return parsed
    record_heal(info, f"{info.field_name}.unreadable_dropped")
    return None


# An optional integer echo (e.g. a traveler count) that is simply absent when
# the model wrote something that is not a number ("2 adults, 1 child").
IntOrNone = BeforeValidator(_int_or_none)


def _upper_code(value: Any, info: ValidationInfo) -> Any:
    if isinstance(value, str) and value != value.strip().upper():
        record_heal(info, f"{info.field_name}.uppercased")
        return value.strip().upper()
    return value


# An ISO-style code the model wrote in the wrong case ("inr").
UpperCode = BeforeValidator(_upper_code)


# --- titles -------------------------------------------------------------------


def clean_title(value: Any) -> Optional[str]:
    """A generated title the trips table can store: one line, trimmed, at most
    ``MAX_TITLE_CHARS``, cut at a word boundary. ``None`` when there is nothing
    usable (blank, or not text at all)."""

    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if len(collapsed) <= MAX_TITLE_CHARS:
        return collapsed or None
    head = collapsed[:MAX_TITLE_CHARS]
    if collapsed[MAX_TITLE_CHARS] != " " and " " in head:
        head = head[: head.rfind(" ")]
    return head.rstrip(" ,;:-–—") or None


def _generated_title(value: Any, info: ValidationInfo) -> Any:
    cleaned = clean_title(value)
    if cleaned != value:
        record_heal(info, f"{info.field_name}.cleaned")
    return cleaned


# A title an agent generated; always storable or absent.
GeneratedTitle = BeforeValidator(_generated_title)


# --- structure ----------------------------------------------------------------


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
