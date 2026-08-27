"""Confirmed logistics anchors and itinerary revision review/accept commands."""

from typing import Any
from uuid import uuid4

from ...schemas.atlas import AtlasRequest
from ...schemas.logistics import LogisticsAnchor, LogisticsConfirmationInput
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_atlas_response
from .atlas_commands import build_working_plan
from .errors import InvalidTripCommandError


async def apply_confirm_logistics(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    confirmation: LogisticsConfirmationInput,
) -> dict[str, Any]:
    itinerary = state["itinerary_state"]
    current_version = itinerary.get("current_version")
    if not current_version:
        raise InvalidTripCommandError(
            "Generate the initial itinerary before confirming logistics."
        )

    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    anchor = LogisticsAnchor(
        id=uuid4(),
        confirmed_at_version=current_version["version"],
        **confirmation.model_dump(),
    )
    anchors = state["logistics_state"].setdefault("anchors", [])
    anchors.append(anchor.model_dump(mode="json"))
    logger.info(
        "Persisted a confirmed logistics anchor.",
        event="be.trip.logistics.anchor_confirmed",
        source="application",
        trip_id=trip_id,
        anchor_id=str(anchor.id),
        anchor_type=anchor.type,
        day_number=anchor.day_number,
        board_item_id=anchor.board_item_id,
    )

    frozen_plan = state["planner_state"]["frozen_plan"]
    working_plan = build_working_plan(frozen_plan["guide_state"])
    request = AtlasRequest.model_validate(
        {
            "trip_context": state["trip_context"],
            "working_plan": working_plan.model_dump(mode="json"),
            "confirmed_anchors": [
                {
                    "type": item["type"],
                    "label": item["label"],
                    "detail": item["detail"],
                    "day_number": item["day_number"],
                }
                for item in anchors
            ],
        }
    )
    request_data = request.model_dump(mode="json")
    logger.info(
        f"Received Atlas request. Request - {logger.format_json(request_data)}",
        event="be.request.validated",
        source="application",
        agent="atlas",
        trip_id=trip_id,
        payload=request_data,
    )
    response = _normalize_atlas_response(
        await engine.atlas(request.model_dump(mode="json"), None)
    )
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        f"Returning Atlas response. Response - {logger.format_json(response_data)}",
        event="be.response.normalized",
        source="application",
        agent="atlas",
        trip_id=trip_id,
        status="success",
        response=response_data,
    )

    proposed_version = current_version["version"] + 1
    new_result = response.model_dump(mode="json")
    affected_days, changes = _diff_days(
        current_version["result"]["final_itinerary"]["days"],
        new_result["final_itinerary"]["days"],
    )
    itinerary["proposed_revision"] = {
        "version": proposed_version,
        "base_version": current_version["version"],
        "result": new_result,
        "affected_days": affected_days,
        "changes": changes,
        "triggered_by": {
            "anchor_id": str(anchor.id),
            "type": anchor.type,
            "label": anchor.label,
        },
    }
    logger.info(
        "Proposed a new itinerary revision from confirmed logistics.",
        event="be.trip.atlas.revision_proposed",
        source="application",
        trip_id=trip_id,
        base_version=current_version["version"],
        proposed_version=proposed_version,
        affected_days=affected_days,
        triggering_anchor_id=str(anchor.id),
    )
    return {
        "message": None,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }


def apply_accept_itinerary_revision(
    logger: TelemetryLogger, state: dict[str, Any]
) -> dict[str, Any]:
    itinerary = state["itinerary_state"]
    proposed = itinerary.get("proposed_revision")
    if not proposed:
        raise InvalidTripCommandError("No proposed itinerary revision to accept.")

    # The outgoing current_version is archived to itinerary_versions (TWM-155)
    # instead of appending to itinerary_state.history — trip_state keeps only
    # the active current_version/proposed_revision.
    outgoing = itinerary["current_version"]
    itinerary["current_version"] = {
        "version": proposed["version"],
        "source_guide_revision": outgoing["source_guide_revision"],
        "result": proposed["result"],
    }
    itinerary["proposed_revision"] = None
    logger.info(
        "Activated a proposed itinerary revision.",
        event="be.trip.atlas.revision_activated",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        version=itinerary["current_version"]["version"],
    )
    return {
        "message": None,
        "agent_meta": None,
        "new_itinerary_version": {
            "version": outgoing["version"],
            "source_guide_revision": outgoing["source_guide_revision"],
            "result": outgoing["result"],
        },
    }


def apply_keep_current_itinerary(
    logger: TelemetryLogger, state: dict[str, Any]
) -> dict[str, Any]:
    itinerary = state["itinerary_state"]
    if not itinerary.get("proposed_revision"):
        raise InvalidTripCommandError(
            "No proposed itinerary revision to keep or discard."
        )
    discarded_version = itinerary["proposed_revision"]["version"]
    itinerary["proposed_revision"] = None
    logger.info(
        "Discarded a proposed itinerary revision; kept the current version.",
        event="be.trip.atlas.revision_discarded",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        discarded_version=discarded_version,
        current_version=itinerary["current_version"]["version"],
    )
    return {"message": None, "agent_meta": None}


def _diff_days(
    old_days: list[dict[str, Any]], new_days: list[dict[str, Any]]
) -> tuple[list[int], list[str]]:
    old_by_number = {day["day_number"]: day for day in old_days}
    affected_days: list[int] = []
    changes: list[str] = []
    for day in new_days:
        number = day["day_number"]
        if old_by_number.get(number) != day:
            affected_days.append(number)
            changes.append(f"Day {number}: {day['title']}")
    return affected_days, changes
