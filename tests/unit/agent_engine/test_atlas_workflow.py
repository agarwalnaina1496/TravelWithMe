"""Atlas workflow and evaluation-corpus contract checks."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_atlas_evaluation_corpus_covers_research_and_authority_boundaries() -> None:
    cases = json.loads(
        (ROOT / "tests" / "resources" / "atlas_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert set(cases_by_id) == {
        "rishikesh-three-day-approved-plan",
        "delhi-agra-jaipur-fixed-route",
        "international-current-rules",
        "specific-hotel-not-found",
        "budget-total-consistency",
        "mode-neutral-transit-language",
        "overnight-stay-price-band-estimate",
    }
    assert cases_by_id["international-current-rules"]["invariants"] == {
        "no_live_search_available": True,
        "do_not_claim_verified": True,
        "use_general_or_unresolved": True,
        "no_booking_claim": True,
    }


def test_atlas_evaluation_corpus_covers_mode_neutral_transit_language() -> None:
    # TWM-203: mode validity is decided downstream by Trusted Actions, so
    # Atlas must never name a transit mode anywhere it describes a
    # movement — this case documents every surface that constraint covers.
    cases = json.loads(
        (ROOT / "tests" / "resources" / "atlas_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert cases_by_id["mode-neutral-transit-language"]["invariants"] == {
        "no_mode_naming_in_title": True,
        "no_mode_naming_in_detail": True,
        "no_mode_naming_in_movement_guidance": True,
        "no_mode_naming_in_budget_notes": True,
        "mode_decided_downstream_by_trusted_actions": True,
    }


def test_atlas_evaluation_corpus_covers_overnight_stay_price_band_estimate() -> None:
    # TWM-204: a multi-day single-base trip must carry a well-formed,
    # ordered tiered estimate for its overnight days.
    cases = json.loads(
        (ROOT / "tests" / "resources" / "atlas_agent_cases.json").read_text(
            encoding="utf-8"
        )
    )
    cases_by_id = {case["id"]: case for case in cases}

    assert cases_by_id["overnight-stay-price-band-estimate"]["invariants"] == {
        "requires_stay_price_estimate_for_overnight_days": True,
        "stay_price_estimate_tiers_ordered": True,
    }


def test_atlas_workflow_has_no_search_tool_or_search_credentials() -> None:
    workflow = json.loads(
        (ROOT / "n8n" / "atlas.json").read_text(encoding="utf-8")
    )
    assert all("SerpApi" not in node["name"] for node in workflow["nodes"])
    assert all("ai_tool" not in value for value in workflow["connections"].values())
