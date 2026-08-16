"""Backend-owned TripState command orchestration and dispatch."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ...persistence.contracts import RecommendationRecord, TripCommandRecord, TripOwner, TripRecord, TripRepository
from ...schemas.trips import TripCommandRequest, TripCommandResponse, TripResponse
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from .atlas_commands import apply_atlas
from .errors import IdempotencyConflictError, InvalidTripCommandError
from .logistics_commands import (
    apply_accept_itinerary_revision,
    apply_confirm_logistics,
    apply_keep_current_itinerary,
)
from .matcher_commands import apply_meridian, select_destination
from .planner_commands import apply_guide, guide_has_started
from .scout_commands import apply_scout
from .state import (
    canonical_state,
    shape_command_trip_state,
    snapshot_touchable_branches,
    touched_branches,
)

_POST_FREEZE_COMMANDS = {
    "start_itinerary",
    "confirm_logistics",
    "accept_itinerary_revision",
    "keep_current_itinerary",
}


@dataclass
class TripCommandService:
    repository: TripRepository
    engine: AgentEngine
    logger: TelemetryLogger

    async def execute(
        self, owner: TripOwner, trip: TripRecord, payload: TripCommandRequest
    ) -> TripCommandResponse:
        request_hash = hashlib.sha256(
            json.dumps(
                payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        prior = await self.repository.get_command(
            owner, trip.id, payload.idempotency_key
        )
        if prior:
            return self._replay(prior, request_hash)

        state = canonical_state(trip.trip_state)
        state["trip_id"] = str(trip.id)
        latest_recommendation = await self.repository.get_latest_recommendation(owner, trip.id)
        before = snapshot_touchable_branches(state)
        result = await self._apply(state, payload, latest_recommendation)
        touched = touched_branches(state, before)
        shaped_trip_state = shape_command_trip_state(state, touched)
        new_recommendation = result.pop("new_recommendation", None)
        new_itinerary_version = result.pop("new_itinerary_version", None)
        response_without_trip = {
            "message": result["message"],
            "agent_meta": result["agent_meta"],
        }
        committed = await self.repository.commit_command(
            owner,
            trip.id,
            payload.expected_version,
            payload.idempotency_key,
            request_hash,
            state,
            shaped_trip_state,
            response_without_trip,
            frozenset(touched),
            new_recommendation,
            new_itinerary_version,
        )
        if committed is None:
            raise LookupError("Trip not found.")
        if isinstance(committed, TripCommandRecord):
            return self._replay(committed, request_hash)
        response = TripCommandResponse(
            trip=TripResponse(
                id=committed.id,
                title=committed.title,
                product_mode=committed.product_mode,
                trip_state=shaped_trip_state,
                ui_state=committed.ui_state,
                version=committed.version,
                created_at=committed.created_at,
                updated_at=committed.updated_at,
            ),
            message=result["message"],
            agent_meta=result["agent_meta"],
        )
        self.logger.info(
            "Committed Backend-owned trip command.",
            event="be.trip.command.committed",
            source="application",
            trip_id=str(trip.id),
            command=payload.command,
            version=committed.version,
            tables_written=sorted({"trips", *touched}),
        )
        if new_recommendation is not None:
            self.logger.info(
                "Archived a new matcher recommendation round.",
                event="be.trip.recommendations.created",
                source="application",
                trip_id=str(trip.id),
                version=new_recommendation["version"],
                option_count=len(new_recommendation.get("options") or []),
            )
        if new_itinerary_version is not None:
            self.logger.info(
                "Archived an itinerary version.",
                event="be.trip.itinerary_versions.archived",
                source="application",
                trip_id=str(trip.id),
                version=new_itinerary_version["version"],
                source_guide_revision=new_itinerary_version["source_guide_revision"],
            )
        return response

    @staticmethod
    def _replay(record: TripCommandRecord, request_hash: str) -> TripCommandResponse:
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency key was already used for a different request."
            )
        return TripCommandResponse.model_validate(record.response)

    async def _apply(
        self,
        state: dict[str, Any],
        payload: TripCommandRequest,
        latest_recommendation: RecommendationRecord | None,
    ) -> dict[str, Any]:
        if (
            state["planner_state"].get("frozen_plan")
            and payload.command not in _POST_FREEZE_COMMANDS
        ):
            raise InvalidTripCommandError(
                "The approved plan is frozen and cannot be changed."
            )
        if payload.command == "start_itinerary":
            return await apply_atlas(self.engine, self.logger, state)
        if payload.command == "confirm_logistics":
            return await apply_confirm_logistics(
                self.engine, self.logger, state, payload.logistics_confirmation
            )
        if payload.command == "accept_itinerary_revision":
            return apply_accept_itinerary_revision(self.logger, state)
        if payload.command == "keep_current_itinerary":
            return apply_keep_current_itinerary(self.logger, state)
        if payload.command == "new_journey":
            state.clear()
            state.update(canonical_state({}))
            return {"message": None, "agent_meta": None}
        if payload.command == "continue":
            if state.get("stage") == "planning" or state.get("active_agent") == "guide":
                if guide_has_started(state):
                    raise InvalidTripCommandError(
                        "Send a traveler message to continue an existing Guide session."
                    )
                return await apply_guide(self.engine, self.logger, state, "START", None, latest_recommendation)
            if state.get("active_agent") == "meridian" or state.get("stage") in {
                "matching", "recommendation_ready", "recommended"
            }:
                return await apply_meridian(self.engine, self.logger, state, None, latest_recommendation)
            return await apply_scout(self.engine, self.logger, state, None, latest_recommendation)
        if payload.command == "select_destination":
            return select_destination(state, payload.option_id or "", latest_recommendation)
        if payload.command == "start_planning":
            if not self._has_planning_destination(state["trip_context"]):
                raise InvalidTripCommandError(
                    "Select or provide a destination before starting planning."
                )
            state["stage"] = "planning"
            state["active_agent"] = "guide"
            return await apply_guide(self.engine, self.logger, state, "START", None, latest_recommendation)
        if payload.command == "approve_plan":
            return await apply_guide(self.engine, self.logger, state, "APPROVE_PLAN", None, latest_recommendation)
        if payload.command == "scout_entry":
            return await apply_scout(self.engine, self.logger, state, payload.message or "", latest_recommendation)
        if payload.command == "discover_entry":
            state["stage"] = "matching"
            state["active_agent"] = "meridian"
            return await apply_meridian(self.engine, self.logger, state, payload.message, latest_recommendation)
        if payload.command == "more_like_this":
            refinement = payload.refinement
            return await apply_meridian(
                self.engine,
                self.logger,
                state,
                refinement.instructions if refinement else None,
                latest_recommendation,
                refinement=refinement.model_dump(mode="json", exclude_none=True)
                if refinement
                else None,
            )
        if payload.command == "known_destination_entry":
            destination = (payload.destination or "").strip()
            if not destination:
                return {
                    "message": "Tell us the destination before starting the plan.",
                    "agent_meta": None,
                }
            state["trip_context"]["destinations"] = [destination]
            state["stage"] = "planning"
            state["active_agent"] = "guide"
            return await apply_guide(self.engine, self.logger, state, "START", None, latest_recommendation)

        message = payload.message or ""
        if state.get("stage") == "planning" or state.get("active_agent") == "guide":
            return await apply_guide(self.engine, self.logger, state, "TRAVELER_MESSAGE", message, latest_recommendation)
        if state.get("active_agent") == "meridian" or state.get("stage") in {
            "matching", "recommendation_ready", "recommended"
        }:
            return await apply_meridian(self.engine, self.logger, state, message, latest_recommendation)
        return await apply_scout(self.engine, self.logger, state, message, latest_recommendation)

    @staticmethod
    def _has_planning_destination(trip_context: dict[str, Any]) -> bool:
        selected = trip_context.get("selected_option")
        if isinstance(selected, dict) and any(
            selected.get(key) for key in ("id", "name")
        ):
            return True
        for key in ("destination", "destinations", "destination_name"):
            value = trip_context.get(key)
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, list) and any(
                isinstance(item, str) and item.strip() for item in value
            ):
                return True
        return False
