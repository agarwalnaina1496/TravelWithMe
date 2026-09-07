"""TWM-217 fitness function.

Every non-structural ``TripView`` field must be produced by a
``TripViewService._compose_<field>`` method that ``build()`` actually calls —
so a future field cannot be wired straight from a router or a repository read,
bypassing the single composer. This test fails loudly if that rule is broken.
"""

import inspect

from twm.schemas.trip_view import TripView
from twm.services.trip_view.service import TripViewService

# id / title / product_mode / version are the trip row's own identity;
# ui_state is a stored per-viewer bag passed through verbatim. Everything
# else is composed.
_STRUCTURAL = {"id", "title", "product_mode", "version", "ui_state"}


def test_every_composed_field_has_a_compose_method_that_build_calls():
    build_source = inspect.getsource(TripViewService.build)
    offenders = []
    for name in TripView.model_fields:
        if name in _STRUCTURAL:
            continue
        method = f"_compose_{name}"
        if not hasattr(TripViewService, method):
            offenders.append(f"{name}: no TripViewService.{method}")
        elif f"self.{method}(" not in build_source:
            offenders.append(f"{name}: {method} exists but build() never calls it")
    assert not offenders, (
        "TripView fields not owned by the composer (add a _compose_* method or, "
        "for a genuinely structural field, add it to _STRUCTURAL): " + "; ".join(offenders)
    )


def test_structural_exemptions_are_still_real_fields():
    assert _STRUCTURAL <= set(TripView.model_fields)
