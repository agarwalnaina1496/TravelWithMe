"""TWM-234: Meridian must never claim `trip_context.destinations`.

That field is Backend-owned on the Discover path (written only by the
deterministic `select_destination` command) and Guide-owned on the
known-destination path (extracted from the traveler's own message) --
Meridian's own extraction must never set it, for a settled choice or a
mere candidate alike. The rule is one function,
`twm.trust_boundary.assert_no_destination_claim`. This module proves
(1) the function rejects a claimed `destinations` value, (2) it allows
everything else, (3) Meridian's delta schema is wired to it, and
(4) Guide's delta schema is deliberately not.
"""

import inspect
from types import SimpleNamespace

import pytest

from twm.schemas.guide import GuideStateDelta
from twm.schemas.meridian import MeridianStateDelta
from twm.trust_boundary import assert_no_destination_claim


def _delta_stub(destinations=None):
    return SimpleNamespace(trip_context=SimpleNamespace(destinations=destinations))


def test_rejects_a_claimed_destinations_value():
    with pytest.raises(ValueError, match="destinations"):
        assert_no_destination_claim(_delta_stub(destinations=["Malta", "Seville"]))


def test_allows_no_destinations_claim():
    assert_no_destination_claim(_delta_stub(destinations=None))


def test_meridian_delta_schema_is_wired_to_the_check():
    validator = MeridianStateDelta.reject_ui_owned_state
    source = inspect.getsource(validator)
    assert "assert_no_destination_claim(self)" in source, (
        "MeridianStateDelta.reject_ui_owned_state must delegate to "
        "assert_no_destination_claim, not re-implement it"
    )


def test_guide_delta_schema_is_deliberately_not_wired_to_the_check():
    # Guide legitimately writes `destinations` on the known-destination
    # path -- it must stay unaffected by this rule.
    validator = GuideStateDelta.reject_ui_owned_state
    source = inspect.getsource(validator)
    assert "assert_no_destination_claim" not in source, (
        "GuideStateDelta must keep writing trip_context.destinations on "
        "the known-destination path -- do not wire it to this check"
    )
