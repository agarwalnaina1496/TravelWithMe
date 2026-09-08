"""TWM-217 / TWM-223 fitness function.

Every non-structural ``TripView`` field must be produced by a
``TripViewService._compose_<field>`` method that ``build()`` actually calls —
so a future field cannot be wired straight from a router or a repository read,
bypassing the single composer.

The rule itself now lives in the reusable
``tests.architecture.composed_fields.assert_all_fields_composed`` helper
(applied here, and available for any future composed response); this module
stays as the ``TripView``-specific entry point.
"""

from tests.architecture.composed_fields import assert_all_fields_composed
from twm.schemas.trip_view import TripView
from twm.services.trip_view.service import TripViewService

# id / title / product_mode / version are the trip row's own identity;
# ui_state is a stored per-viewer bag passed through verbatim. Everything
# else is composed.
_STRUCTURAL = {"id", "title", "product_mode", "version", "ui_state"}


def test_every_composed_field_has_a_compose_method_that_build_calls():
    assert_all_fields_composed(TripView, TripViewService, structural=_STRUCTURAL)


def test_structural_exemptions_are_still_real_fields():
    assert _STRUCTURAL <= set(TripView.model_fields)
