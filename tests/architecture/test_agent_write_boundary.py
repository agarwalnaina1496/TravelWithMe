"""TWM-223: the agent write-boundary fitness function.

An agent's ``state_delta`` may carry only the traveler-context and
operational-memory branches it owns. Backend-owned deterministic state —
lifecycle ``stage`` / ``active_agent``, destination ``selected_option``,
``booking_setup``, ``itinerary_state`` versioning, stored ``recommendations``
history — is never agent-writable, on any branch.

The rule is one function, ``twm.trust_boundary.assert_agent_delta_within_boundary``.
This module proves (1) the function rejects each Backend-owned key, and
(2) every agent delta schema is actually wired to it.
"""

import inspect
from types import SimpleNamespace

import pytest

from twm.schemas.guide import GuideStateDelta
from twm.schemas.meridian import MeridianStateDelta
from twm.schemas.scout import ScoutStateDelta
from twm.trust_boundary import (
    UI_OWNED_STATE_KEYS,
    assert_agent_delta_within_boundary,
)

AGENT_DELTA_SCHEMAS = (ScoutStateDelta, MeridianStateDelta, GuideStateDelta)


def _delta_stub(*, trip_context_extra=None, dict_branches=None):
    """A minimal stand-in for a validated agent delta model — a
    ``trip_context`` exposing ``model_extra`` plus any free-form dict
    branches — so the boundary function is exercised without constructing
    the real (contract-owned) schema models."""

    return SimpleNamespace(
        trip_context=SimpleNamespace(model_extra=dict(trip_context_extra or {})),
        **(dict_branches or {}),
    )


@pytest.mark.parametrize("key", sorted(UI_OWNED_STATE_KEYS))
def test_rejects_a_backend_owned_key_in_trip_context_extras(key):
    with pytest.raises(ValueError, match=key):
        assert_agent_delta_within_boundary(_delta_stub(trip_context_extra={key: "x"}))


@pytest.mark.parametrize("key", sorted(UI_OWNED_STATE_KEYS))
def test_rejects_a_backend_owned_key_in_a_dict_branch(key):
    with pytest.raises(ValueError, match=key):
        assert_agent_delta_within_boundary(
            _delta_stub(dict_branches={"matcher_state": {key: "x", "conversation_context": {}}})
        )


def test_allows_owned_branches():
    assert_agent_delta_within_boundary(
        _delta_stub(
            trip_context_extra={"origin_city": "Delhi", "budget": "modest"},
            dict_branches={"matcher_state": {"conversation_context": {"awaiting": "budget"}}},
        )
    )


def test_the_key_set_is_the_backend_owned_lifecycle_state():
    assert UI_OWNED_STATE_KEYS == frozenset(
        {"stage", "active_agent", "selected_option", "booking_setup", "itinerary_state", "recommendations"}
    )


@pytest.mark.parametrize("delta_cls", AGENT_DELTA_SCHEMAS)
def test_every_agent_delta_schema_is_wired_to_the_boundary_check(delta_cls):
    validator = delta_cls.reject_ui_owned_state
    source = inspect.getsource(validator)
    assert "assert_agent_delta_within_boundary(self)" in source, (
        f"{delta_cls.__name__}.reject_ui_owned_state must delegate to the "
        "shared trust_boundary check, not re-implement it"
    )
