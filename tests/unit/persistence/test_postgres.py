"""PostgresTripRepository construction-time guardrails."""

import pytest

from twm.persistence.postgres import PostgresTripRepository


@pytest.mark.parametrize("schema", ["twm_app", "app", "twm_app_v2", "_private"])
def test_accepts_a_well_formed_schema_name(schema: str) -> None:
    repository = PostgresTripRepository(pool=None, schema=schema)

    assert repository.schema == schema


@pytest.mark.parametrize(
    "schema",
    [
        "twm-app",
        "Twm_App",
        "2twm",
        "twm app",
        "twm_app; DROP TABLE trips;--",
        "",
    ],
)
def test_rejects_a_malformed_schema_name(schema: str) -> None:
    with pytest.raises(ValueError):
        PostgresTripRepository(pool=None, schema=schema)
