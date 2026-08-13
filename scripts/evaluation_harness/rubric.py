"""Evaluate one case's invariants against a normalized AgentExecution response."""

from typing import Any, Callable

from .fixtures import EvaluationCase

CheckFn = Callable[[EvaluationCase, dict[str, Any], Any], None]


class RubricFailure(AssertionError):
    """One invariant did not hold against the normalized response."""


# Invariant keys with no automatable check against the response shape alone.
# This is the single source of truth for "known gap" vs. "someone added a new
# case with an invariant key nobody wrote a check for" — see
# tests/unit/evaluation_harness/test_harness.py's
# test_missing_rubric_checks_match_known_gaps, which fails if this set drifts
# from what the case files actually declare.
KNOWN_UNIMPLEMENTED_INVARIANTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("scout", "advisor_message_stored_in_conversation_context"),
        # Whether a transport mode was excluded via ordinary criteria
        # evaluation versus a hardcoded rule is a judgment call the schema
        # cannot distinguish mechanically; verify manually per TWM-125.
        ("meridian", "no_hardcoded_transport_mode_exclusion"),
        # Complete round-trip cost accounting requires reading the actual
        # cost narrative in evaluation details; not automatable from shape
        # alone. Verify manually per TWM-125.
        ("meridian", "requires_complete_round_trip_accounting"),
    }
)


def evaluate(case: EvaluationCase, response: dict[str, Any]) -> None:
    checks = _CHECKS[case.agent]
    for key, expected in case.invariants.items():
        if key not in checks:
            raise NotImplementedError(f"no rubric check for invariant key: {key}")
        checks[key](case, response, expected)


def missing_checks(cases: list[EvaluationCase]) -> set[tuple[str, str]]:
    """Every (agent, invariant key) pair declared in cases with no rubric check."""

    missing: set[tuple[str, str]] = set()
    for case in cases:
        checks = _CHECKS[case.agent]
        for key in case.invariants:
            if key not in checks:
                missing.add((case.agent, key))
    return missing


def _casefold_set(values: list[str]) -> set[str]:
    return {value.casefold() for value in values}


# ---- scout ---------------------------------------------------------------


def _scout_intent(case: EvaluationCase, response: dict[str, Any], expected: str) -> None:
    if response.get("intent") != expected:
        raise RubricFailure(f"expected intent {expected!r}, got {response.get('intent')!r}")


def _scout_extracts_trip_context(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    has_context = bool(response.get("state_delta", {}).get("trip_context"))
    if has_context != expected:
        raise RubricFailure("expected scout to extract trip_context into state_delta")


def _scout_no_stage_transition(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    # ScoutAgentOutput has no stage field, so scout can never write UI-owned stage.
    if "stage" in response.get("state_delta", {}) and expected:
        raise RubricFailure("scout state_delta must not contain stage")


def _scout_must_not_set_selected_option(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    has_selected = "selected_option" in response.get("state_delta", {}).get(
        "trip_context", {}
    )
    if has_selected and expected:
        raise RubricFailure("scout must not write selected_option")


def _scout_must_clear_selected_option(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    _scout_must_not_set_selected_option(case, response, expected)


def _scout_routes_to_meridian(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if (response.get("intent") == "matcher") != expected:
        raise RubricFailure("expected scout to route to meridian")


def _scout_routes_to_guide(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if (response.get("intent") == "planner") != expected:
        raise RubricFailure("expected scout to route to guide")


def _scout_preserves_trip_context(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    # A destructive rewrite of trip_context is not a preserving delta.
    delta = response.get("state_delta", {}).get("trip_context", {})
    if "destinations" in delta and not delta.get("destinations"):
        raise RubricFailure("scout cleared destinations while claiming to preserve context")


# ---- meridian --------------------------------------------------------------


def _meridian_status(case: EvaluationCase, response: dict[str, Any], expected: str) -> None:
    if response.get("status") != expected:
        raise RubricFailure(f"expected status {expected!r}, got {response.get('status')!r}")


def _meridian_trip_type(case: EvaluationCase, response: dict[str, Any], expected: str) -> None:
    if response.get("trip_type") != expected:
        raise RubricFailure(f"expected trip_type {expected!r}, got {response.get('trip_type')!r}")


def _meridian_requires_traveler_criteria(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if bool(response.get("traveler_criteria")) != expected:
        raise RubricFailure("expected traveler_criteria to be present")


def _meridian_requires_ranked_options(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    options = response.get("options", [])
    ranks = [option["rank"] for option in options]
    if expected and ranks != list(range(1, len(ranks) + 1)):
        raise RubricFailure("expected sequential ranked options starting at 1")


def _meridian_awaiting_cleared(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    awaiting = response.get("state_delta", {}).get("matcher_state", {}).get(
        "conversation_context", {}
    ).get("awaiting")
    if expected and awaiting is not None:
        raise RubricFailure("expected awaiting to be cleared")


def _meridian_requires_awaiting_reason(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    awaiting = response.get("state_delta", {}).get("matcher_state", {}).get(
        "conversation_context", {}
    ).get("awaiting")
    if expected and not (isinstance(awaiting, str) and awaiting.strip()):
        raise RubricFailure("expected a non-empty awaiting reason")


def _meridian_message_matches_last(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    last = response.get("state_delta", {}).get("matcher_state", {}).get(
        "conversation_context", {}
    ).get("last_meridian_message")
    if expected and last != response.get("message"):
        raise RubricFailure("expected message to match last_meridian_message")


def _meridian_no_options_allowed(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and response.get("options"):
        raise RubricFailure("expected no recommendation options")


def _meridian_requires_visible_tradeoff(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if not expected:
        return
    for option in response.get("options", []):
        has_tradeoff = bool(option.get("other_considerations")) or any(
            evaluation["outcome"] in {"TRADEOFF", "MISMATCH"}
            for evaluation in option.get("evaluations", [])
        )
        if not has_tradeoff:
            raise RubricFailure(f"option {option.get('name')} has no visible trade-off")


def _meridian_allows_constraint_adjustments(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and not response.get("constraint_adjustment_suggestions"):
        raise RubricFailure("expected constraint_adjustment_suggestions")


def _meridian_reject_written_recommendations(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    has_recommendations = "recommendations" in response.get("state_delta", {}).get(
        "matcher_state", {}
    )
    if expected and has_recommendations:
        raise RubricFailure("meridian must not write recommendation history")


def _meridian_reject_written_selected_option(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    has_selected = "selected_option" in response.get("state_delta", {}).get(
        "trip_context", {}
    )
    if expected and has_selected:
        raise RubricFailure("meridian must not write selected_option")


# ---- guide -------------------------------------------------------------
#
# Guide returns a state_delta (only what changed this turn) rather than a
# full-state echo — an omitted field means Backend keeps the prior value.
# These checks evaluate the *effective* post-turn state (input state with
# the delta applied on top), matching what planner_commands.py actually
# produces, so a check like "preserve_places" correctly passes whether
# Guide re-sent the places list unchanged or simply omitted it.


def _guide_effective_trip_context(
    case: EvaluationCase, response: dict[str, Any]
) -> dict[str, Any]:
    effective = dict(case.input.get("trip_context", {}))
    effective.update(response.get("state_delta", {}).get("trip_context", {}))
    return effective


def _guide_effective_planner_state(
    case: EvaluationCase, response: dict[str, Any]
) -> dict[str, Any]:
    effective = dict(case.input.get("planner_state", {}))
    delta = response.get("state_delta", {}).get("planner_state", {})
    for key in ("places", "day_plan", "conversation_context"):
        if key in delta:
            effective[key] = delta[key]
    return effective


def _guide_awaiting(
    case: EvaluationCase, response: dict[str, Any], expected: str | None
) -> None:
    actual = _guide_effective_planner_state(case, response).get(
        "conversation_context", {}
    ).get("awaiting")
    if actual != expected:
        raise RubricFailure(f"expected awaiting {expected!r}, got {actual!r}")


def _guide_places_omitted_from_delta(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    has_places = "places" in response.get("state_delta", {}).get("planner_state", {})
    if expected and has_places:
        raise RubricFailure(
            "expected places to be omitted from the delta (untouched this turn)"
        )


def _guide_destinations(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    actual = _guide_effective_trip_context(case, response).get("destinations", [])
    if _casefold_set(actual) != _casefold_set(expected):
        raise RubricFailure(f"expected destinations {expected!r}, got {actual!r}")


def _guide_duration_days(
    case: EvaluationCase, response: dict[str, Any], expected: int
) -> None:
    actual = _guide_effective_trip_context(case, response).get("duration_days")
    if actual != expected:
        raise RubricFailure(f"expected duration_days {expected!r}, got {actual!r}")


def _guide_day_plan_length(
    case: EvaluationCase, response: dict[str, Any], expected: int
) -> None:
    day_plan = _guide_effective_planner_state(case, response).get("day_plan", [])
    if len(day_plan) != expected:
        raise RubricFailure(f"expected day_plan length {expected!r}, got {len(day_plan)}")


def _guide_must_exclude_places(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    places = _casefold_set(_guide_effective_planner_state(case, response).get("places", []))
    for place in expected:
        if place.casefold() in places:
            raise RubricFailure(f"expected {place!r} to be excluded from places")


def _guide_must_include_exclusions(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    exclusions = _casefold_set(_guide_effective_trip_context(case, response).get("exclusions", []))
    for exclusion in expected:
        if exclusion.casefold() not in exclusions:
            raise RubricFailure(f"expected exclusions to include {exclusion!r}")


def _guide_preserve_places(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    places = _casefold_set(_guide_effective_planner_state(case, response).get("places", []))
    for place in expected:
        if place.casefold() not in places:
            raise RubricFailure(f"expected {place!r} to be preserved in places")


def _guide_preserve_all_places(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    input_places = case.input.get("planner_state", {}).get("places", [])
    actual = _guide_effective_planner_state(case, response).get("places", [])
    if expected and _casefold_set(actual) != _casefold_set(input_places):
        raise RubricFailure("expected every input place to be preserved in the response")


def _guide_place_only_day_plan(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    effective = _guide_effective_planner_state(case, response)
    places = _casefold_set(effective.get("places", []))
    allocated = [
        place
        for day in effective.get("day_plan", [])
        for place in day.get("places", [])
    ]
    if expected and _casefold_set(allocated) != places:
        raise RubricFailure("expected day plan to allocate exactly the approved places")


def _guide_preserve_destination_order(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    actual = _guide_effective_trip_context(case, response).get("destinations", [])
    if [value.casefold() for value in actual] != [value.casefold() for value in expected]:
        raise RubricFailure(f"expected destination order {expected!r}, got {actual!r}")


def _guide_requires_day_pace(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    day_plan = _guide_effective_planner_state(case, response).get("day_plan", [])
    if expected and any(day.get("pace") not in {"relaxed", "balanced", "packed"} for day in day_plan):
        raise RubricFailure("expected every day plan entry to carry a valid pace signal")


def _guide_must_not_add_destinations(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    input_destinations = _casefold_set(case.input.get("trip_context", {}).get("destinations", []))
    actual = _casefold_set(_guide_effective_trip_context(case, response).get("destinations", []))
    if expected and not actual.issubset(input_destinations):
        raise RubricFailure("expected guide not to add destinations beyond trip_context")


# ---- atlas ---------------------------------------------------------------


def _atlas_destinations(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    actual = response.get("final_itinerary", {}).get("trip_summary", {}).get(
        "destinations", []
    )
    if _casefold_set(actual) != _casefold_set(expected):
        raise RubricFailure(f"expected destinations {expected!r}, got {actual!r}")


def _atlas_duration_days(
    case: EvaluationCase, response: dict[str, Any], expected: int
) -> None:
    actual = response.get("final_itinerary", {}).get("trip_summary", {}).get(
        "duration_days"
    )
    if actual != expected:
        raise RubricFailure(f"expected duration_days {expected!r}, got {actual!r}")


def _atlas_preserve_approved_places(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if not expected:
        return
    days = response.get("final_itinerary", {}).get("days", [])
    all_text = " ".join(
        item.get("title", "") + " " + item.get("location", "")
        for day in days
        for item in day.get("timeline", [])
    ).casefold()
    approved = case.input.get("working_plan", {}).get("approved_places", [])
    missing = [place for place in approved if place.casefold() not in all_text]
    if missing:
        raise RubricFailure(f"expected approved places to appear in itinerary: {missing!r}")


def _atlas_exclude(
    case: EvaluationCase, response: dict[str, Any], expected: list[str]
) -> None:
    days = response.get("final_itinerary", {}).get("days", [])
    all_text = " ".join(
        f"{item.get('title', '')} {item.get('detail', '')}"
        for day in days
        for item in day.get("timeline", [])
    ).casefold()
    for excluded in expected:
        if excluded.casefold() in all_text:
            raise RubricFailure(f"expected {excluded!r} to be excluded from the itinerary")


def _atlas_requires_day_timeline(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    days = response.get("final_itinerary", {}).get("days", [])
    if expected and (not days or any(not day.get("timeline") for day in days)):
        raise RubricFailure("expected every day to have a non-empty timeline")


def _atlas_requires_budget_lines(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    lines = response.get("final_itinerary", {}).get("budget_summary", {}).get("lines", [])
    if expected and not lines:
        raise RubricFailure("expected non-empty budget lines")


def _atlas_requires_assumptions_for_missing_start_date(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    start_date = case.input.get("working_plan", {}).get("start_date")
    assumptions = response.get("final_itinerary", {}).get("assumptions", [])
    if expected and start_date is None and not assumptions:
        raise RubricFailure(
            "expected an explicit assumption when the working plan has no start date"
        )


def _atlas_preserve_destination_order(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    input_order = case.input.get("working_plan", {}).get("destinations", [])
    actual = response.get("final_itinerary", {}).get("trip_summary", {}).get(
        "destinations", []
    )
    if expected and [v.casefold() for v in actual] != [v.casefold() for v in input_order]:
        raise RubricFailure(f"expected destination order {input_order!r}, got {actual!r}")


def _atlas_do_not_add_destinations(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    input_destinations = _casefold_set(case.input.get("working_plan", {}).get("destinations", []))
    actual = _casefold_set(
        response.get("final_itinerary", {}).get("trip_summary", {}).get("destinations", [])
    )
    if expected and not actual.issubset(input_destinations):
        raise RubricFailure("expected atlas not to add destinations beyond the working plan")


def _atlas_no_live_search_available(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and _atlas_has_verified_reference(response):
        raise RubricFailure(
            "expected no VERIFIED references when live search is unavailable"
        )


def _atlas_do_not_claim_verified(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and _atlas_has_verified_reference(response):
        raise RubricFailure("expected no reference to claim VERIFIED status")


def _atlas_use_general_or_unresolved(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    has_general = _atlas_has_general_reference(response)
    has_unresolved = bool(response.get("unresolved"))
    if expected and not (has_general or has_unresolved):
        raise RubricFailure("expected general guidance or an unresolved item")


def _atlas_no_booking_claim(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and _atlas_has_verified_reference(response):
        raise RubricFailure("expected no verified booking claim")


def _atlas_do_not_invent_url(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and _atlas_has_verified_reference(response):
        raise RubricFailure("expected no invented VERIFIED source_url")


def _atlas_allow_general_guidance(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and not _atlas_has_general_reference(response):
        raise RubricFailure("expected at least one GENERAL_GUIDANCE reference")


def _atlas_record_unresolved(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and not response.get("unresolved"):
        raise RubricFailure("expected a non-empty unresolved list")


def _atlas_no_deep_link(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if expected and _atlas_has_verified_reference(response):
        raise RubricFailure("expected no deep link from a VERIFIED reference")


def _atlas_backend_recalculates_totals(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    if not expected:
        return
    budget = response.get("final_itinerary", {}).get("budget_summary", {})
    lines = budget.get("lines", [])
    expected_low = sum(line["amount_low"] for line in lines)
    expected_high = sum(line["amount_high"] for line in lines)
    if budget.get("total_low") != expected_low or budget.get("total_high") != expected_high:
        raise RubricFailure("expected totals to equal the sum of budget lines")


def _atlas_non_negative_ranges(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    lines = response.get("final_itinerary", {}).get("budget_summary", {}).get("lines", [])
    if expected and any(
        line["amount_low"] < 0 or line["amount_high"] < 0 for line in lines
    ):
        raise RubricFailure("expected non-negative budget ranges")


def _atlas_single_currency(
    case: EvaluationCase, response: dict[str, Any], expected: bool
) -> None:
    currency = response.get("final_itinerary", {}).get("budget_summary", {}).get("currency")
    if expected and not currency:
        raise RubricFailure("expected a single declared currency")


def _atlas_has_verified_reference(response: dict[str, Any]) -> bool:
    return any(
        reference.get("status") == "VERIFIED"
        for reference in _atlas_all_references(response)
    )


def _atlas_has_general_reference(response: dict[str, Any]) -> bool:
    return any(
        reference.get("status") == "GENERAL_GUIDANCE"
        for reference in _atlas_all_references(response)
    )


def _atlas_all_references(response: dict[str, Any]) -> list[dict[str, Any]]:
    itinerary = response.get("final_itinerary", {})
    references = [
        option.get("reference", {}) for option in itinerary.get("travel_options", [])
    ]
    references += [
        option.get("reference", {}) for option in itinerary.get("stay_options", [])
    ]
    references += [
        item.get("reference", {})
        for day in itinerary.get("days", [])
        for item in day.get("timeline", [])
    ]
    references += [
        note.get("reference", {}) for note in itinerary.get("practical_notes", [])
    ]
    return [reference for reference in references if reference]


_CHECKS: dict[str, dict[str, CheckFn]] = {
    "scout": {
        "intent": _scout_intent,
        "extracts_trip_context": _scout_extracts_trip_context,
        "no_stage_transition": _scout_no_stage_transition,
        "must_not_set_selected_option": _scout_must_not_set_selected_option,
        "must_clear_selected_option": _scout_must_clear_selected_option,
        "routes_to_meridian": _scout_routes_to_meridian,
        "routes_to_guide": _scout_routes_to_guide,
        "preserves_trip_context": _scout_preserves_trip_context,
    },
    "meridian": {
        "status": _meridian_status,
        "trip_type": _meridian_trip_type,
        "requires_traveler_criteria": _meridian_requires_traveler_criteria,
        "requires_ranked_options": _meridian_requires_ranked_options,
        "awaiting_cleared": _meridian_awaiting_cleared,
        "requires_awaiting_reason": _meridian_requires_awaiting_reason,
        "message_matches_last_meridian_message": _meridian_message_matches_last,
        "no_options_allowed": _meridian_no_options_allowed,
        "requires_visible_tradeoff": _meridian_requires_visible_tradeoff,
        "allows_constraint_adjustment_suggestions": _meridian_allows_constraint_adjustments,
        "reject_agent_written_recommendations": _meridian_reject_written_recommendations,
        "reject_agent_written_selected_option": _meridian_reject_written_selected_option,
    },
    "guide": {
        "awaiting": _guide_awaiting,
        "places_omitted_from_delta": _guide_places_omitted_from_delta,
        "destinations": _guide_destinations,
        "duration_days": _guide_duration_days,
        "day_plan_length": _guide_day_plan_length,
        "must_exclude_places": _guide_must_exclude_places,
        "must_include_exclusions": _guide_must_include_exclusions,
        "preserve_places": _guide_preserve_places,
        "preserve_all_places": _guide_preserve_all_places,
        "place_only_day_plan": _guide_place_only_day_plan,
        "preserve_destination_order": _guide_preserve_destination_order,
        "must_not_add_destinations": _guide_must_not_add_destinations,
        "requires_day_pace": _guide_requires_day_pace,
    },
    "atlas": {
        "destinations": _atlas_destinations,
        "duration_days": _atlas_duration_days,
        "preserve_approved_places": _atlas_preserve_approved_places,
        "exclude": _atlas_exclude,
        "requires_day_timeline": _atlas_requires_day_timeline,
        "requires_budget_lines": _atlas_requires_budget_lines,
        "requires_assumptions_for_missing_start_date": _atlas_requires_assumptions_for_missing_start_date,
        "preserve_destination_order": _atlas_preserve_destination_order,
        "do_not_add_destinations": _atlas_do_not_add_destinations,
        "no_live_search_available": _atlas_no_live_search_available,
        "do_not_claim_verified": _atlas_do_not_claim_verified,
        "use_general_or_unresolved": _atlas_use_general_or_unresolved,
        "no_booking_claim": _atlas_no_booking_claim,
        "do_not_invent_url": _atlas_do_not_invent_url,
        "allow_general_guidance": _atlas_allow_general_guidance,
        "record_unresolved": _atlas_record_unresolved,
        "no_deep_link": _atlas_no_deep_link,
        "backend_recalculates_totals": _atlas_backend_recalculates_totals,
        "non_negative_ranges": _atlas_non_negative_ranges,
        "single_currency": _atlas_single_currency,
    },
}
