"""Contract/security tests for the trusted travel-action shape (TWM-130).

TWM-130 is contract-only: no router/service/resolver exists yet (TWM-131 is
"implement resolvers", not started), so there is no FastAPI boundary to
drive these validators through the way `tests/api/apitest_flight_search.py`
does for TWM-144's flight-search contract. This repo's AGENTS.md normally
disallows unit tests that instantiate request/response schema models
directly, in favor of API-boundary tests — but with no API surface at all
in this increment, the only way to exercise these validators is realistic
construction attempts that must be rejected. That is what this file does,
analogous in spirit to how `flight_search.py`'s FlightSearchResponse status
shape is exercised, just one layer lower since no HTTP layer exists yet.
"""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from twm.schemas.atlas import AtlasReference
from twm.schemas.trusted_action import (
    ActionTarget,
    ModeFeasibility,
    TripFeasibilityAssessment,
    TrustedAction,
    TrustedActionDisabledDetail,
    TrustedActionMissingInputDetail,
    TrustedActionRequest,
    TrustedActionResult,
    TrustedActionUnsupportedPartnerDetail,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _search_redirect_action(**overrides):
    fields = dict(
        action_type="SEARCH_REDIRECT",
        domain="train",
        target=ActionTarget(partner="ixigo", path="search/train", query_params={"from": "DEL", "to": "BOM"}),
        affiliate_disclosure=True,
        origin="Delhi",
        destination="Mumbai",
        departure_date=date(2026, 9, 10),
        trip_shape="one_way",
        generated_at=NOW,
    )
    fields.update(overrides)
    return TrustedAction(**fields)


# --- URL normalization / safe scheme ---------------------------------------


def test_target_url_normalizes_to_https_allowlisted_domain():
    action = _search_redirect_action()
    assert action.target.target_url == "https://www.ixigo.com/search/train?from=DEL&to=BOM"


def test_target_url_scheme_is_always_https_no_field_can_override_it():
    # ActionTarget has no scheme input field at all — attempting to smuggle
    # one in as an extra field must be rejected outright (extra="forbid").
    with pytest.raises(ValidationError):
        ActionTarget(partner="ixigo", path="search", scheme="http")


def test_path_containing_another_url_is_rejected():
    with pytest.raises(ValidationError):
        ActionTarget(partner="ixigo", path="redirect?u=http://evil.example.com")


def test_query_param_value_containing_a_url_is_rejected_open_redirect_guard():
    with pytest.raises(ValidationError):
        ActionTarget(partner="ixigo", path="search", query_params={"next": "https://evil.example.com"})


def test_query_param_value_with_javascript_scheme_is_rejected():
    with pytest.raises(ValidationError):
        ActionTarget(partner="ixigo", path="search", query_params={"onclick": "javascript:alert(1)"})


def test_target_has_no_raw_url_input_field_no_escape_hatch():
    with pytest.raises(ValidationError):
        ActionTarget(partner="ixigo", path="search", target_url="https://attacker.example.com")


# --- Allowlist / authorization -----------------------------------------------


def test_partner_outside_closed_allowlist_enum_is_rejected():
    with pytest.raises(ValidationError):
        ActionTarget(partner="expedia", path="search")


def test_partner_not_approved_for_domain_is_rejected():
    with pytest.raises(ValidationError):
        _search_redirect_action(domain="train", target=ActionTarget(partner="hotellook", path="search"))


def test_flight_search_redirect_via_ixigo_succeeds_as_a_second_alternative():
    # A second, alternative option alongside the live Aviasales
    # CHECK_PRICES offer — never a replacement for it.
    action = _search_redirect_action(
        domain="flight", target=ActionTarget(partner="ixigo", path="flights", query_params={"from": "DEL", "to": "BOM"})
    )
    assert action.target.partner == "ixigo"


def test_flight_provider_action_is_rejected_check_prices_owns_that_path():
    with pytest.raises(ValidationError):
        TrustedAction(
            action_type="PROVIDER",
            domain="flight",
            target=ActionTarget(partner="ixigo", path="flights"),
            affiliate_disclosure=True,
            generated_at=NOW,
        )


def test_ixigo_is_approved_for_stay_alongside_the_dedicated_stay_partners():
    action = _search_redirect_action(domain="stay", target=ActionTarget(partner="ixigo", path="hotels"))
    assert action.target.partner == "ixigo"


# --- Affiliate disclosure -----------------------------------------------------


def test_provider_action_missing_affiliate_disclosure_true_is_rejected():
    with pytest.raises(ValidationError):
        TrustedAction(
            action_type="PROVIDER",
            domain="stay",
            target=ActionTarget(partner="hotellook", path="hotels"),
            affiliate_disclosure=False,
            generated_at=NOW,
        )


def test_search_redirect_with_affiliate_disclosure_true_succeeds():
    action = _search_redirect_action()
    assert action.affiliate_disclosure is True


# --- CHECK_PRICES / internal capability shape --------------------------------


def test_check_prices_requires_internal_capability_not_a_target():
    with pytest.raises(ValidationError):
        TrustedAction(
            action_type="CHECK_PRICES",
            domain="flight",
            target=ActionTarget(partner="ixigo", path="flights"),
            affiliate_disclosure=False,
            generated_at=NOW,
        )


def test_check_prices_with_internal_capability_succeeds():
    action = TrustedAction(
        action_type="CHECK_PRICES",
        domain="flight",
        internal_capability="flight_search",
        affiliate_disclosure=False,
        generated_at=NOW,
    )
    assert action.target is None


def test_search_redirect_requires_a_target_not_internal_capability():
    with pytest.raises(ValidationError):
        TrustedAction(
            action_type="SEARCH_REDIRECT",
            domain="train",
            internal_capability="flight_search",
            affiliate_disclosure=False,
            generated_at=NOW,
        )


# --- No price/rating/availability escape hatch -------------------------------


def test_trusted_action_has_no_price_field_at_all():
    action = _search_redirect_action()
    dumped = action.model_dump()
    for forbidden in ("price", "rating", "availability", "booking_confirmed"):
        assert forbidden not in dumped


def test_trusted_action_rejects_unknown_price_field_extra_forbid():
    with pytest.raises(ValidationError):
        _search_redirect_action(price=100)


# --- Canonical identity / route-date invariants ------------------------------


def test_same_origin_and_destination_is_rejected():
    with pytest.raises(ValidationError):
        _search_redirect_action(origin="Delhi", destination="delhi")


def test_one_way_action_with_return_date_is_rejected():
    with pytest.raises(ValidationError):
        _search_redirect_action(trip_shape="one_way", return_date=date(2026, 9, 20))


def test_request_missing_all_route_fields_is_permitted_not_a_422_equivalent():
    # Permissive at the request/generation-input stage — a resolver (TWM-131)
    # is responsible for turning this into a deterministic missing_input
    # outcome, not this schema's validator.
    request = TrustedActionRequest(action_type="SEARCH_REDIRECT", domain="train")
    assert request.origin is None


# --- Result envelope status-shape discriminator ------------------------------


def test_resolved_status_requires_action_and_forbids_other_payloads():
    with pytest.raises(ValidationError):
        TrustedActionResult(status="resolved", generated_at=NOW)


def test_missing_input_status_requires_missing_input_payload():
    with pytest.raises(ValidationError):
        TrustedActionResult(status="missing_input", generated_at=NOW)
    result = TrustedActionResult(
        status="missing_input",
        generated_at=NOW,
        missing_input=TrustedActionMissingInputDetail(
            missing_fields=["origin", "destination"], message="Tell us where you're going."
        ),
    )
    assert result.action is None


def test_missing_input_status_forbids_action_payload():
    with pytest.raises(ValidationError):
        TrustedActionResult(
            status="missing_input",
            generated_at=NOW,
            missing_input=TrustedActionMissingInputDetail(missing_fields=["origin"], message="x"),
            action=_search_redirect_action(),
        )


def test_unsupported_partner_status_requires_its_payload():
    with pytest.raises(ValidationError):
        TrustedActionResult(status="unsupported_partner", generated_at=NOW)
    result = TrustedActionResult(
        status="unsupported_partner",
        generated_at=NOW,
        unsupported_partner=TrustedActionUnsupportedPartnerDetail(
            domain="stay", requested_partner="ixigo", message="ixigo is not approved for stays."
        ),
    )
    assert result.status == "unsupported_partner"


def test_disabled_status_requires_its_payload():
    with pytest.raises(ValidationError):
        TrustedActionResult(status="disabled", generated_at=NOW)
    result = TrustedActionResult(
        status="disabled",
        generated_at=NOW,
        disabled=TrustedActionDisabledDetail(reason="Partner temporarily disabled."),
    )
    assert result.status == "disabled"


def test_resolved_status_with_action_succeeds():
    result = TrustedActionResult(status="resolved", generated_at=NOW, action=_search_redirect_action())
    assert result.action.target.target_url.startswith("https://www.ixigo.com/")


# --- Evidence vs. action structural separation -------------------------------


def test_atlas_reference_cannot_be_built_from_a_trusted_action_payload():
    action = _search_redirect_action()
    with pytest.raises(ValidationError):
        AtlasReference(**action.model_dump())


def test_trusted_action_cannot_be_built_from_an_atlas_reference_payload():
    reference = AtlasReference(status="GENERAL_GUIDANCE")
    with pytest.raises(ValidationError):
        TrustedAction(**reference.model_dump())


# --- Feasibility: honesty tag for a deterministic rule judgement (TWM-195
# root-fix contract -- modes only ever holds feasible entries; there is no
# excluded_modes field and no ruled_out/unknown status to construct) -------


def test_feasible_mode_requires_a_verification_tag():
    with pytest.raises(ValidationError):
        ModeFeasibility(
            mode="train",
            status="feasible",
            duration_source="computed",
            estimated_duration_minutes=600,
            reason="Overnight train service exists on this route.",
        )


def test_feasible_mode_can_never_claim_verified_never_fact_checked():
    with pytest.raises(ValidationError):
        ModeFeasibility(
            mode="bus",
            status="feasible",
            duration_source="computed",
            estimated_duration_minutes=480,
            reason="Bus operators run this route daily.",
            verification=AtlasReference(
                status="VERIFIED", source_title="Operator timetable", source_url="https://example.com/timetable"
            ),
        )


def test_feasible_mode_with_general_guidance_succeeds():
    feasibility = ModeFeasibility(
        mode="bus",
        status="feasible",
        duration_source="computed",
        estimated_duration_minutes=480,
        reason="Bus operators run this route daily.",
        verification=AtlasReference(status="GENERAL_GUIDANCE"),
    )
    assert feasibility.verification.status == "GENERAL_GUIDANCE"


def test_computed_duration_over_bound_is_rejected():
    with pytest.raises(ValidationError):
        ModeFeasibility(
            mode="train",
            status="feasible",
            duration_source="computed",
            estimated_duration_minutes=100000,
            reason="Extremely long overland route.",
            verification=AtlasReference(status="GENERAL_GUIDANCE"),
        )


def test_invalid_status_literal_is_rejected():
    # status is now always "feasible" (TWM-195 root-fix contract) --
    # ruled_out/unknown no longer exist on the contract.
    with pytest.raises(ValidationError):
        ModeFeasibility(
            mode="drive",
            status="ruled_out",
            duration_source="computed",
            reason="x",
            verification=AtlasReference(status="GENERAL_GUIDANCE"),
        )


def test_invalid_duration_source_literal_is_rejected():
    # duration_source is now always "computed" (TWM-195) -- the old
    # "llm_estimated" source no longer exists on the contract.
    with pytest.raises(ValidationError):
        ModeFeasibility(
            mode="drive",
            status="feasible",
            duration_source="llm_estimated",
            reason="x",
            verification=AtlasReference(status="GENERAL_GUIDANCE"),
        )


def test_feasibility_assessment_rejects_duplicate_modes_within_the_list():
    with pytest.raises(ValidationError):
        TripFeasibilityAssessment(
            modes=[
                ModeFeasibility(
                    mode="flight",
                    status="feasible",
                    duration_source="computed",
                    reason="Direct route.",
                    verification=AtlasReference(status="GENERAL_GUIDANCE"),
                ),
                ModeFeasibility(
                    mode="flight",
                    status="feasible",
                    duration_source="computed",
                    reason="Duplicate.",
                    verification=AtlasReference(status="GENERAL_GUIDANCE"),
                ),
            ],
        )


def test_trip_feasibility_assessment_has_no_excluded_modes_field():
    # Regression (TWM-195 root-fix contract): excluded_modes was removed
    # entirely, not merely emptied by default.
    assert "excluded_modes" not in TripFeasibilityAssessment.model_fields


def test_feasibility_assessment_allows_an_empty_modes_list():
    # A route can genuinely have zero feasible modes (fail-closed cannot-
    # assess outcome) -- this must not be a validation error.
    assessment = TripFeasibilityAssessment(modes=[])
    assert assessment.modes == []


def test_feasibility_assessment_with_multiple_feasible_modes_succeeds():
    assessment = TripFeasibilityAssessment(
        modes=[
            ModeFeasibility(
                mode="flight",
                status="feasible",
                duration_source="computed",
                reason="Direct route.",
                verification=AtlasReference(status="GENERAL_GUIDANCE"),
            ),
            ModeFeasibility(
                mode="train",
                status="feasible",
                duration_source="computed",
                estimated_duration_minutes=600,
                reason="Overnight train service exists.",
                verification=AtlasReference(status="GENERAL_GUIDANCE"),
            ),
        ],
    )
    assert len(assessment.modes) == 2
