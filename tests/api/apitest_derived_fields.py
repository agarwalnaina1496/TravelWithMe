"""Derived fields stay in the published API contract but not in the schema the
model is asked to fill in (TWM-234)."""

import json

from fastapi.testclient import TestClient

from twm.schemas import AtlasAgentOutput, MeridianAgentOutput
from twm.schemas.agent_contract import llm_output_schema


def test_the_published_response_schemas_still_describe_every_derived_field(
    api_client: TestClient,
) -> None:
    schemas = api_client.get("/openapi.json").json()["components"]["schemas"]

    assert "trip_type" in schemas["MeridianResponse"]["properties"]
    assert "trip_duration" in schemas["AtlasTripSummary"]["properties"]
    assert {"total_low", "total_high"} <= set(schemas["AtlasBudgetSummary"]["properties"])
    assert "requires_advance_booking" in schemas["AtlasTimelineItem"]["properties"]


def test_the_schema_shown_to_the_model_leaves_derived_fields_out() -> None:
    meridian = json.dumps(llm_output_schema(MeridianAgentOutput))
    atlas = json.dumps(llm_output_schema(AtlasAgentOutput))

    for name in ("trip_type",):
        assert f'"{name}"' not in meridian
    for name in ("trip_duration", "total_low", "total_high", "requires_advance_booking"):
        assert f'"{name}"' not in atlas
    # What the model does decide stays.
    assert '"rank"' in meridian
    assert '"booking_readiness"' in atlas
