"""Atlas slips heal on the first attempt; the itinerary is reviewed against the approved plan (TWM-234)."""

import asyncio
import json
from copy import deepcopy

import pytest

from tests.unit.agent_engine.test_service import service_with_outputs

from tests.unit.agent_engine.tolerance_support import (
    run,
    atlas,
    first_item,
)


def unsourced_verified(output: dict) -> dict:
    first_item(output)["reference"] = {"status": "VERIFIED"}
    return output


def empty_stay_estimate(output: dict) -> dict:
    output["final_itinerary"]["days"][0]["stay_price_estimate"] = []
    return output


def unordered_stay_estimate(output: dict) -> dict:
    tier = lambda name, low, high: {"tier": name, "estimated_cost_low": low, "estimated_cost_high": high}  # noqa: E731
    output["final_itinerary"]["days"][0]["stay_price_estimate"] = [
        tier("premium", 3000, 5000), tier("budget", 500, 900), tier("mid_range", 1500, 2500),
    ]
    return output


def wrong_duration_echo(output: dict) -> dict:
    output["final_itinerary"]["trip_summary"]["trip_duration"] += 3
    return output


def misnumbered_days(output: dict) -> dict:
    for day in output["final_itinerary"]["days"]:
        day["day_number"] += 4
    return output


def readiness_without_the_flag(output: dict) -> dict:
    first_item(output).update(requires_advance_booking=False, booking_readiness="suggested")
    return output


def flag_without_readiness(output: dict) -> dict:
    first_item(output).update(requires_advance_booking=True, booking_readiness=None)
    return output


def lone_cost_bound(output: dict) -> dict:
    first_item(output).update(estimated_cost_low=500, estimated_cost_high=None)
    return output


def assumptions_inside_summary(output: dict) -> dict:
    output["final_itinerary"]["trip_summary"]["assumptions"] = [{"category": "other", "detail": "Mid-range stay."}]
    return output


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (unsourced_verified, lambda r: first_item(r)["reference"]["status"] == "GENERAL_GUIDANCE"),
        (empty_stay_estimate, lambda r: "stay_price_estimate" not in r["final_itinerary"]["days"][0]),
        (unordered_stay_estimate,
         lambda r: [t["tier"] for t in r["final_itinerary"]["days"][0]["stay_price_estimate"]] == ["budget", "mid_range", "premium"]),
        (wrong_duration_echo,
         lambda r: r["final_itinerary"]["trip_summary"]["trip_duration"] == len(r["final_itinerary"]["days"])),
        (misnumbered_days,
         lambda r: [d["day_number"] for d in r["final_itinerary"]["days"]] == list(range(1, len(r["final_itinerary"]["days"]) + 1))),
        (assumptions_inside_summary,
         lambda r: {"category": "other", "detail": "Mid-range stay."} in r["final_itinerary"]["assumptions"]
         and "assumptions" not in r["final_itinerary"]["trip_summary"]),
        (lambda o: first_item(o).update(note="extra") or o, lambda r: "note" not in first_item(r)),
        (readiness_without_the_flag,
         lambda r: first_item(r)["booking_readiness"] == "suggested" and first_item(r)["requires_advance_booking"] is True),
        (flag_without_readiness,
         lambda r: first_item(r)["booking_readiness"] == "needs_advance_booking" and first_item(r)["requires_advance_booking"] is True),
        (lone_cost_bound,
         lambda r: "estimated_cost_low" not in first_item(r) and "estimated_cost_high" not in first_item(r)),
    ],
)
def test_atlas_slips_heal_on_the_first_attempt(monkeypatch, mutate, check):
    response, attempts, _ = run(monkeypatch, "atlas", mutate(atlas()))

    assert attempts == 1
    assert check(response)


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda o: first_item(o).update(kind="activity", reference={"status": "General_Guidance"}),
         lambda r: first_item(r)["kind"] == "ACTIVITY"),
        (lambda o: first_item(o).update(backup_plan="", movement_guidance="", start_time=" "),
         lambda r: "movement_guidance" not in first_item(r) and "start_time" not in first_item(r)),
        (lambda o: o["final_itinerary"]["days"][0].update(notes=None, backup_plan=""),
         lambda r: r["final_itinerary"]["days"][0]["notes"] == []),
        (lambda o: o["final_itinerary"].update(assumptions=None, sources=None, practical_notes=None),
         lambda r: r["final_itinerary"]["assumptions"] == [] and r["final_itinerary"]["sources"] == []),
        (lambda o: first_item(o).update(estimated_cost_low=1250.5, estimated_cost_high="1,800"),
         lambda r: first_item(r)["estimated_cost_low"] == 1250 or first_item(r)["estimated_cost_low"] == 1251),
        (lambda o: o["final_itinerary"]["trip_summary"].update(num_travelers="2 adults, 1 child"),
         lambda r: "num_travelers" not in r["final_itinerary"]["trip_summary"]),
    ],
)
def test_atlas_type_slips_heal_on_the_first_attempt(monkeypatch, mutate, check):
    output = atlas()
    mutate(output)
    response, attempts, _ = run(monkeypatch, "atlas", output)

    assert attempts == 1
    assert check(response)


def test_an_itinerary_with_the_wrong_number_of_days_is_retried_with_the_rule_stated(monkeypatch):
    from twm.schemas.atlas import AtlasWorkingPlan
    from twm.services.trip_commands.atlas_commands import atlas_review

    base = atlas()
    expected = len(base["final_itinerary"]["days"])
    plan = AtlasWorkingPlan.model_validate(
        {
            "destinations": ["Goa"],
            "trip_duration": expected,
            "days": [{"day_number": number, "places": []} for number in range(1, expected + 1)],
        }
    )
    longer = deepcopy(base)
    longer["final_itinerary"]["days"].append(deepcopy(base["final_itinerary"]["days"][0]))
    engine, adapter = service_with_outputs(monkeypatch, json.dumps(longer), json.dumps(base))

    result = asyncio.run(engine.atlas({}, None, review=atlas_review(plan)))

    sent = [call.args[1].system_prompt for call in adapter.invoke.await_args_list]
    assert len(result.response["final_itinerary"]["days"]) == expected
    assert f"exactly {expected} days" in sent[1]


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda o: first_item(o).update(estimated_cost_low=2000, estimated_cost_high=800),
         lambda r: (first_item(r)["estimated_cost_low"], first_item(r)["estimated_cost_high"]) == (800, 2000)),
        (lambda o: o["final_itinerary"]["budget_summary"]["lines"][0].update(amount_low=900, amount_high=300),
         lambda r: r["final_itinerary"]["budget_summary"]["lines"][0]["amount_low"] == 300),
    ],
)
def test_atlas_inverted_ranges_are_put_the_right_way_round(monkeypatch, mutate, check):
    output = atlas()
    mutate(output)
    response, attempts, healed = run(monkeypatch, "atlas", output)

    assert attempts == 1
    assert check(response)
    assert any(name.endswith("range_swapped") for name in healed)


def test_a_stringly_typed_advance_booking_flag_is_still_read(monkeypatch):
    output = atlas()
    first_item(output).update(requires_advance_booking="true", booking_readiness=None)

    response, attempts, _ = run(monkeypatch, "atlas", output)

    assert attempts == 1
    assert first_item(response)["booking_readiness"] == "needs_advance_booking"
