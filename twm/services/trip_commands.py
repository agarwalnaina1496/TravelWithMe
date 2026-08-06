"""Backend-owned TripState command orchestration."""

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ..persistence.contracts import (
    TripCommandRecord,
    TripRecord,
    TripRepository,
)
from ..schemas.guide import GuideRequest
from ..schemas.meridian import MeridianRequest
from ..schemas.scout import ScoutRequest
from ..schemas.trips import TripCommandRequest, TripCommandResponse, TripResponse
from ..telemetry import TelemetryLogger
from .agent_engine import AgentEngine
from .response_normalization import (
    _normalize_guide_response,
    _normalize_meridian_response,
    _normalize_scout_response,
)


class IdempotencyConflictError(ValueError):
    pass


class InvalidTripCommandError(ValueError):
    pass


def _trip_response(record: TripRecord) -> TripResponse:
    return TripResponse.model_validate(record, from_attributes=True)


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            _deep_merge(current, value)
        else:
            target[key] = copy.deepcopy(value)


def _merge_operational_state(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key == "rejected_options" and isinstance(value, list):
            current = target.setdefault(key, [])
            for item in value:
                if item not in current:
                    current.append(copy.deepcopy(item))
        elif isinstance(value, dict):
            child = target.setdefault(key, {})
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            _merge_operational_state(child, value)
        else:
            target[key] = copy.deepcopy(value)


def _canonical_state(value: dict[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(value)
    state.setdefault("status", "free")
    state.setdefault("stage", "new")
    state.setdefault("active_agent", "scout")
    state.setdefault("trip_context", {})
    state.setdefault("advisor_state", {"conversation_context": {}, "artifacts": []})
    state.setdefault("matcher_state", {"conversation_context": {}, "recommendations": []})
    state.setdefault("planner_state", {})
    return state


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

        state = _canonical_state(trip.trip_state)
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
            trip=_trip_response(committed),
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
        if payload.command == "new_journey":
            state.clear()
            state.update(_canonical_state({}))
            return {"message": None, "agent_meta": None}
        if payload.command == "select_destination":
            return self._select_destination(state, payload.option_id or "")
        if payload.command == "start_planning":
            if not self._has_planning_destination(state["trip_context"]):
                raise InvalidTripCommandError(
                    "Select or provide a destination before starting planning."
                )
            state["stage"] = "planning"
            state["active_agent"] = "guide"
            return await self._guide(state, "START", None)
        if payload.command == "approve_places":
            return await self._guide(state, "APPROVE_PLACES", None)
        if payload.command == "approve_plan":
            return await self._guide(state, "APPROVE_PLAN", None)

        message = payload.message or ""
        if state.get("stage") == "planning" or state.get("active_agent") == "guide":
            return await self._guide(state, "TRAVELER_MESSAGE", message)
        if state.get("active_agent") == "meridian" or state.get("stage") in {
            "matching", "recommendation_ready", "recommended"
        }:
            return await self._meridian(state, message)
        return await self._scout(state, message)

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

    async def _scout(self, state: dict[str, Any], message: str) -> dict[str, Any]:
        phase = {
            "stage": state["stage"],
            "trip_context": state["trip_context"],
            "advisor_state": {
                "conversation_context": state["advisor_state"].get(
                    "conversation_context", {}
                )
            },
        }
        request = ScoutRequest.model_validate({"trip_state": phase, "message": message})
        response = _normalize_scout_response(
            await self.engine.scout(request.trip_state.model_dump(mode="json"), request.message)
        )
        delta = dict(response.state_delta.trip_context)
        delta.pop("selected_option", None)
        _deep_merge(state["trip_context"], delta)
        if response.message:
            advisor = state["advisor_state"]
            advisor.setdefault("conversation_context", {})[
                "last_advisor_message"
            ] = response.message
            advisor.setdefault("artifacts", []).append(
                {
                    "type": "advice",
                    "source": "scout",
                    "assistant_message": response.message,
                    "agent_meta": response.agent_meta.model_dump(mode="json"),
                }
            )
        if response.intent == "matcher":
            state["stage"] = "matching"
            state["active_agent"] = "meridian"
            return await self._meridian(state, message)
        if response.intent == "planner":
            state["stage"] = "planning"
            state["active_agent"] = "guide"
            return await self._guide(state, "TRAVELER_MESSAGE", message)
        state["active_agent"] = "scout"
        return {
            "message": response.message,
            "agent_meta": response.agent_meta.model_dump(mode="json"),
        }

    async def _meridian(self, state: dict[str, Any], message: str | None) -> dict[str, Any]:
        latest = (state["matcher_state"].get("recommendations") or [None])[-1]
        prior_options = [] if not latest else [
            {
                "rank": option["rank"], "name": option["name"],
                "type": option["type"],
                **({"circuit_id": option["circuit_id"]} if option["type"] == "circuit" else {"destination_id": option["destination_id"]}),
            }
            for option in latest.get("options", [])
        ]
        phase = {
            "trip_context": state["trip_context"],
            "advisor_state": {
                "conversation_context": state["advisor_state"].get("conversation_context", {})
            },
            "matcher_state": {
                "conversation_context": state["matcher_state"].get("conversation_context", {}),
                "prior_recommendations": prior_options,
                "rejected_options": state["matcher_state"].get("rejected_options", []),
            },
        }
        request = MeridianRequest.model_validate({"trip_state": phase, "message": message})
        response = _normalize_meridian_response(
            await self.engine.meridian(request.trip_state.model_dump(mode="json"), request.message)
        )
        trip_delta = dict(response.state_delta.trip_context)
        trip_delta.pop("selected_option", None)
        _deep_merge(state["trip_context"], trip_delta)
        matcher_delta = dict(response.state_delta.matcher_state)
        matcher_delta.pop("recommendations", None)
        _merge_operational_state(state["matcher_state"], matcher_delta)
        if response.status == "NEEDS_CLARIFICATION":
            state["stage"] = "matching"
            state["active_agent"] = "meridian"
        else:
            payload = response.model_dump(mode="json", exclude={"state_delta"}, exclude_none=True)
            state["matcher_state"].setdefault("recommendations", []).append(payload)
            state["stage"] = "recommended"
            state["active_agent"] = None
        return {
            "message": response.message,
            "agent_meta": response.agent_meta.model_dump(mode="json"),
        }

    async def _guide(
        self, state: dict[str, Any], event: str, message: str | None
    ) -> dict[str, Any]:
        session = state["planner_state"].get("guide_session", {})
        phase = {
            "trip_context": state["trip_context"],
            "guide_state": session.get("state", {}),
            "guide_event": event,
        }
        request = GuideRequest.model_validate(
            {"event": event, "trip_state": {"trip_context": phase["trip_context"], "guide_state": phase["guide_state"]}, "message": message}
        )
        agent_state = request.trip_state.model_dump(mode="json")
        agent_state["guide_event"] = request.event
        response = _normalize_guide_response(
            await self.engine.guide(agent_state, request.message)
        )
        revision = int(session.get("revision", 0)) + 1
        state["planner_state"]["guide_session"] = {
            "state": response.guide_state.model_dump(mode="json"),
            "revision": revision,
        }
        state["active_agent"] = "guide"
        state["stage"] = "planned" if response.guide_state.phase == "PLAN_APPROVED" else "planning"
        return {
            "message": response.message,
            "agent_meta": response.agent_meta.model_dump(mode="json"),
        }

    @staticmethod
    def _select_destination(state: dict[str, Any], option_id: str) -> dict[str, Any]:
        recommendations = state["matcher_state"].get("recommendations") or []
        if not recommendations:
            raise InvalidTripCommandError("No recommendation is available to select.")
        option = next(
            (
                item
                for item in recommendations[-1].get("options", [])
                if option_id in {item.get("destination_id"), item.get("circuit_id")}
            ),
            None,
        )
        if option is None:
            raise InvalidTripCommandError("Selected option is not in the latest recommendations.")
        identity = option.get("circuit_id") or option.get("destination_id")
        state["trip_context"]["selected_option"] = {
            "type": option["type"], "id": identity, "name": option["name"]
        }
        state["stage"] = "matched"
        state["active_agent"] = None
        return {"message": f"{option['name']} is confirmed.", "agent_meta": None}
