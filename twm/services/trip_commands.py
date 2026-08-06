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
    object_branches = {
        "trip_context": {},
        "advisor_state": {"conversation_context": {}, "artifacts": []},
        "matcher_state": {"conversation_context": {}, "recommendations": []},
        "planner_state": {},
    }
    for name, default in object_branches.items():
        if not isinstance(state.get(name), dict):
            state[name] = copy.deepcopy(default)
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
        if state["planner_state"].get("frozen_plan"):
            raise InvalidTripCommandError(
                "The approved plan is frozen and cannot be changed."
            )
        if payload.command == "new_journey":
            state.clear()
            state.update(_canonical_state({}))
            return {"message": None, "agent_meta": None}
        if payload.command == "continue":
            if state.get("stage") == "planning" or state.get("active_agent") == "guide":
                session = state["planner_state"].get("guide_session", {})
                if session.get("state"):
                    raise InvalidTripCommandError(
                        "Send a traveler message to continue an existing Guide session."
                    )
                return await self._guide(state, "START", None)
            if state.get("active_agent") == "meridian" or state.get("stage") in {
                "matching", "recommendation_ready", "recommended"
            }:
                return await self._meridian(state, None)
            return await self._scout(state, None)
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

    async def _scout(self, state: dict[str, Any], message: str | None) -> dict[str, Any]:
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
            state["trip_context"].pop("selected_option", None)
            state["stage"] = "matching"
            state["active_agent"] = "meridian"
            return await self._meridian(state, message)
        if response.intent == "planner":
            state["stage"] = "planning"
            state["active_agent"] = "guide"
            return await self._guide(state, "START", None)
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
        planner = state["planner_state"]
        if planner.get("frozen_plan"):
            raise InvalidTripCommandError(
                "The approved plan is frozen and cannot be changed."
            )

        session = planner.get("guide_session", {})
        prior_state = session.get("state", {})
        prior_phase = prior_state.get("phase")
        clarification_resume_phase = session.get("clarification_resume_phase")
        clarification_base_state = session.get("clarification_base_state")
        self._validate_guide_event(event, prior_phase)
        guide_input_state = prior_state
        if prior_phase == "NEEDS_CLARIFICATION" and isinstance(
            clarification_base_state, dict
        ):
            guide_input_state = copy.deepcopy(clarification_base_state)
            guide_input_state["phase"] = "NEEDS_CLARIFICATION"
            guide_input_state["pending_clarification"] = prior_state.get(
                "pending_clarification"
            )
        phase = {
            "trip_context": state["trip_context"],
            "guide_state": guide_input_state,
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
        replacement = response.guide_state.model_dump(mode="json")
        self._validate_guide_transition(
            event,
            prior_state,
            replacement,
            clarification_resume_phase=clarification_resume_phase,
            clarification_base_state=clarification_base_state,
            explicit_changes=response.explicit_changes,
        )
        revision = int(session.get("revision", 0)) + 1
        next_session = {
            "state": replacement,
            "revision": revision,
            "explicit_changes": list(response.explicit_changes),
        }
        if response.guide_state.phase == "NEEDS_CLARIFICATION":
            next_session["clarification_resume_phase"] = (
                clarification_resume_phase
                or ("DAY_PLAN_DRAFT" if event == "APPROVE_PLACES" else None)
                or (
                    prior_phase
                    if prior_phase in {"PLACES_DRAFT", "DAY_PLAN_DRAFT"}
                    else "PLACES_DRAFT"
                )
            )
            base_state = (
                copy.deepcopy(clarification_base_state)
                if isinstance(clarification_base_state, dict)
                else copy.deepcopy(prior_state)
            )
            if event == "TRAVELER_MESSAGE":
                for field in response.explicit_changes:
                    if field != "day_plan":
                        base_state[field] = copy.deepcopy(replacement[field])
            if base_state.get("phase") in {"PLACES_DRAFT", "DAY_PLAN_DRAFT"}:
                next_session["clarification_base_state"] = base_state
        planner["guide_session"] = next_session
        if response.guide_state.phase == "PLAN_APPROVED":
            planner["frozen_plan"] = {
                "guide_revision": revision,
                "guide_state": copy.deepcopy(replacement),
            }
            state["active_agent"] = None
            state["stage"] = "planned"
        else:
            state["active_agent"] = "guide"
            state["stage"] = "planning"
        self.logger.info(
            "Applied Backend-owned Guide revision.",
            event="be.trip.guide.revision_applied",
            source="application",
            trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
            guide_event=event,
            guide_phase=response.guide_state.phase,
            guide_revision=revision,
            frozen=response.guide_state.phase == "PLAN_APPROVED",
            explicit_changes=list(response.explicit_changes),
        )
        return {
            "message": response.message,
            "agent_meta": response.agent_meta.model_dump(mode="json"),
        }

    @staticmethod
    def _validate_guide_event(event: str, prior_phase: str | None) -> None:
        if event == "START":
            if prior_phase is not None:
                raise InvalidTripCommandError("Guide planning has already started.")
            return
        if prior_phase is None:
            raise InvalidTripCommandError("Start Guide planning before changing the plan.")
        if event == "APPROVE_PLACES" and prior_phase != "PLACES_DRAFT":
            raise InvalidTripCommandError("Only the latest places draft can be approved.")
        if event == "APPROVE_PLAN" and prior_phase != "DAY_PLAN_DRAFT":
            raise InvalidTripCommandError("Only the latest day plan can be approved.")

    @staticmethod
    def _validate_guide_transition(
        event: str,
        prior_state: dict[str, Any],
        replacement: dict[str, Any],
        *,
        clarification_resume_phase: str | None,
        clarification_base_state: dict[str, Any] | None,
        explicit_changes: list[str],
    ) -> None:
        next_phase = replacement.get("phase")
        prior_phase = prior_state.get("phase")
        traveler_phases = {
            "NEEDS_CLARIFICATION": {
                "NEEDS_CLARIFICATION",
                clarification_resume_phase or "PLACES_DRAFT",
            },
            "PLACES_DRAFT": {"NEEDS_CLARIFICATION", "PLACES_DRAFT"},
            "DAY_PLAN_DRAFT": {"NEEDS_CLARIFICATION", "DAY_PLAN_DRAFT"},
        }
        allowed_phases = {
            "START": {"NEEDS_CLARIFICATION", "PLACES_DRAFT"},
            "TRAVELER_MESSAGE": traveler_phases.get(prior_phase, set()),
            "APPROVE_PLACES": {"NEEDS_CLARIFICATION", "DAY_PLAN_DRAFT"},
            "APPROVE_PLAN": {"PLAN_APPROVED"},
        }[event]
        if next_phase not in allowed_phases:
            raise InvalidTripCommandError(
                f"Guide returned invalid phase {next_phase} for {event}."
            )
        if event != "TRAVELER_MESSAGE" and explicit_changes:
            raise InvalidTripCommandError(
                f"Guide declared traveler changes for non-traveler event {event}."
            )
        comparison_state = prior_state
        preserved_fields: tuple[str, ...] = ()
        if event in {"APPROVE_PLACES", "APPROVE_PLAN"}:
            preserved_fields = (
                "destinations",
                "duration_days",
                "start_date",
                "places",
                "preferences",
                "exclusions",
            )
            if event == "APPROVE_PLAN":
                preserved_fields += ("day_plan",)
        elif event == "TRAVELER_MESSAGE":
            preserved_fields = (
                "destinations",
                "duration_days",
                "start_date",
                "places",
                "day_plan",
                "preferences",
                "exclusions",
            )
            if isinstance(clarification_base_state, dict):
                comparison_state = clarification_base_state
            if next_phase == "NEEDS_CLARIFICATION":
                preserved_fields = tuple(
                    field for field in preserved_fields if field != "day_plan"
                )
        changed = [
            field
            for field in preserved_fields
            if replacement.get(field) != comparison_state.get(field)
        ]
        undeclared = [field for field in changed if field not in explicit_changes]
        if undeclared:
            raise InvalidTripCommandError(
                "Guide changed traveler decisions without declaring them: "
                + ", ".join(undeclared)
                + "."
            )

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
