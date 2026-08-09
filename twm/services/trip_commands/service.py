"""Backend-owned TripState command orchestration and dispatch."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ...persistence.contracts import TripCommandRecord, TripRecord, TripRepository
from ...schemas.trips import TripCommandRequest, TripCommandResponse
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from .errors import IdempotencyConflictError, InvalidTripCommandError
from .matcher_commands import apply_meridian, select_destination
from .planner_commands import apply_guide
from .scout_commands import apply_scout
from .state import canonical_state, trip_response


@dataclass
class TripCommandService:
    repository: TripRepository
    engine: AgentEngine
    logger: TelemetryLogger

    async def execute(
        self, guest_id: UUID, trip: TripRecord, payload: TripCommandRequest
    ) -> TripCommandResponse:
        request_hash = hashlib.sha256(
            json.dumps(
                payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        prior = await self.repository.get_command(
            guest_id, trip.id, payload.idempotency_key
        )
        if prior:
            return self._replay(prior, request_hash)

        state = canonical_state(trip.trip_state)
        state["trip_id"] = str(trip.id)
        result = await self._apply(state, payload)
        response_without_trip = {
            "message": result["message"],
            "agent_meta": result["agent_meta"],
        }
        committed = await self.repository.commit_command(
            guest_id,
            trip.id,
            payload.expected_version,
            payload.idempotency_key,
            request_hash,
            state,
            response_without_trip,
        )
        if committed is None:
            raise LookupError("Trip not found.")
        if isinstance(committed, TripCommandRecord):
            return self._replay(committed, request_hash)
        response = TripCommandResponse(
            trip=trip_response(committed),
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
        self, state: dict[str, Any], payload: TripCommandRequest
    ) -> dict[str, Any]:
        if state["planner_state"].get("frozen_plan"):
            raise InvalidTripCommandError(
                "The approved plan is frozen and cannot be changed."
            )
        if payload.command == "new_journey":
            state.clear()
            state.update(canonical_state({}))
            return {"message": None, "agent_meta": None}
        if payload.command == "continue":
            if state.get("stage") == "planning" or state.get("active_agent") == "guide":
                session = state["planner_state"].get("guide_session", {})
                if session.get("state"):
                    raise InvalidTripCommandError(
                        "Send a traveler message to continue an existing Guide session."
                    )
                return await apply_guide(self.engine, self.logger, state, "START", None)
            if state.get("active_agent") == "meridian" or state.get("stage") in {
                "matching", "recommendation_ready", "recommended"
            }:
                return await apply_meridian(self.engine, state, None)
            return await apply_scout(self.engine, self.logger, state, None)
        if payload.command == "select_destination":
            return select_destination(state, payload.option_id or "")
        if payload.command == "start_planning":
            if not self._has_planning_destination(state["trip_context"]):
                raise InvalidTripCommandError(
                    "Select or provide a destination before starting planning."
                )
            state["stage"] = "planning"
            state["active_agent"] = "guide"
            return await apply_guide(self.engine, self.logger, state, "START", None)
        if payload.command == "approve_places":
            return await apply_guide(self.engine, self.logger, state, "APPROVE_PLACES", None)
        if payload.command == "approve_plan":
            return await apply_guide(self.engine, self.logger, state, "APPROVE_PLAN", None)

        message = payload.message or ""
        if state.get("stage") == "planning" or state.get("active_agent") == "guide":
            return await apply_guide(self.engine, self.logger, state, "TRAVELER_MESSAGE", message)
        if state.get("active_agent") == "meridian" or state.get("stage") in {
            "matching", "recommendation_ready", "recommended"
        }:
            return await apply_meridian(self.engine, state, message)
        return await apply_scout(self.engine, self.logger, state, message)

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
