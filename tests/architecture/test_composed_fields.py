"""TWM-223: the single-source composition fitness function.

Applies ``assert_all_fields_composed`` to ``TripView`` (the check that used
to be inline in ``tests/unit/trip_view/test_composer_owns_every_field.py``),
and proves the helper actually fails on an un-composed field.
"""

import pytest
from pydantic import BaseModel

from tests.architecture.composed_fields import assert_all_fields_composed
from twm.schemas.trip_view import TripView
from twm.services.trip_view.service import TripViewService

# id / title / product_mode / version are the trip row's own identity;
# ui_state is a stored per-viewer bag passed through verbatim.
_TRIP_VIEW_STRUCTURAL = {"id", "title", "product_mode", "version", "ui_state"}


def test_trip_view_every_field_is_owned_by_the_composer():
    assert_all_fields_composed(
        TripView, TripViewService, structural=_TRIP_VIEW_STRUCTURAL
    )


def test_helper_fails_on_an_uncomposed_field():
    class _StubResponse(BaseModel):
        composed: str
        leaked: str

    class _StubComposer:
        def build(self):
            self._compose_composed()  # `leaked` is wired straight, bypassing compose

        def _compose_composed(self):  # pragma: no cover - reflection only
            ...

    with pytest.raises(AssertionError, match="leaked"):
        assert_all_fields_composed(_StubResponse, _StubComposer)


def test_helper_fails_when_build_never_calls_the_compose_method():
    class _StubResponse(BaseModel):
        value: str

    class _StubComposer:
        def build(self):
            return "wired straight from a repo read"

        def _compose_value(self):  # pragma: no cover - reflection only
            ...

    with pytest.raises(AssertionError, match="never calls it"):
        assert_all_fields_composed(_StubResponse, _StubComposer)
