"""Backend-owned TripState command orchestration and dispatch."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ...persistence.contracts import RecommendationRecord, TripCommandRecord, TripOwner, TripRecord, TripRepository
from ...schemas.trip_context import DESTINATIONS_KEY
from ...schemas.trips import TripCommandRequest, TripCommandResponse, TripFirstMessageRequest, TripResponse
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from .atlas_commands import apply_atlas
from .booking_commands import apply_update_booking_dates
from .traveler_commands import apply_update_traveler_composition
from .errors import IdempotencyConflictError, InvalidTripCommandError
from .logistics_commands import (
    apply_accept_itinerary_revision,
    apply_confirm_logistics,
    apply_keep_current_itinerary,
)
from .matcher_commands import apply_meridian, select_destination
from .planner_commands import (
    apply_guide,
    apply_reopen_fresh,
    apply_reopen_revisit,
    guide_has_started,
    has_pending_reopen_choice,
)
from .state import (
    canonical_state,
    set_stage,
    shape_command_trip_state,
    snapshot_touchable_branches,
    touched_branches,
)

_POST_FREEZE_COMMANDS = {
    "start_itinerary",
    "confirm_logistics",
    "accept_itinerary_revision",
    "keep_current_itinerary",
    "update_booking_dates",
    "update_traveler_composition",
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

    async def execute_first_message(
        self, owner: TripOwner, payload: TripFirstMessageRequest
    ) -> TripCommandResponse:
        """TWM-189: runs the traveler's first turn entirely in memory —
        against a state with no trip_id yet — and only calls
        repository.create_trip() if that agent call succeeds. No DB row
        exists to roll back or delete on failure, so sequencing alone
        prevents orphan rows.

        Always a traveler_message-shaped turn (payload.entry_intent decides
        Meridian vs. Guide) — the only turn that can ever start a trip with
        no trip_id yet. Safe to persist via create_trip() alone either way:
        Guide's turn is gated behind six required inputs before it can ever
        produce a day_plan (twm/prompts/guide.md), and Meridian's turn is
        gated the same way before it can ever produce a recommendation
        (twm/prompts/meridian.md, TWM-189) — so neither can produce a
        recommendation/itinerary archive-table row on a first turn that
        repository.create_trip() would have no path to persist.
        """
        state = canonical_state({})
        command_payload = TripCommandRequest(
            command="traveler_message",
            expected_version=1,
            idempotency_key=uuid4(),
            message=payload.message,
            entry_intent=payload.entry_intent,
        )
        try:
            result = await self._apply(state, command_payload, None)
        except Exception as error:
            self.logger.warning(
                "First-message agent call failed; no trip was created.",
                event="be.trip.first_message.failed",
                source="application",
                entry_intent=payload.entry_intent,
                error_type=type(error).__name__,
                detail=str(error)[:500],
            )
            raise
        for leaked_key in ("new_recommendation", "new_itinerary_version"):
            if result.pop(leaked_key, None) is not None:
                self.logger.warning(
                    "First-message agent turn produced an archive-table "
                    "result before any trip existed to archive it against; "
                    "discarding it — this should be unreachable once the "
                    "Meridian/Guide gates hold.",
                    event="be.trip.first_message.unexpected_archive_result",
                    source="application",
                    entry_intent=payload.entry_intent,
                    leaked_key=leaked_key,
                )
        trip = await self.repository.create_trip(
            owner.guest_session_id, owner.user_id, payload.title, payload.product_mode, state, {}
        )
        self.logger.info(
            "Created trip from first-message orchestration.",
            event="be.trip.created",
            source="application",
            trip_id=str(trip.id),
            entry_intent=payload.entry_intent,
            version=trip.version,
        )
        return TripCommandResponse(
            trip=TripResponse(
                id=trip.id, title=trip.title, product_mode=trip.product_mode,
                trip_state=trip.trip_state, ui_state=trip.ui_state, version=trip.version,
                created_at=trip.created_at, updated_at=trip.updated_at,
            ),
            message=result["message"],
            agent_meta=result.get("agent_meta"),
        )

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
        if payload.command == "update_booking_dates":
            return apply_update_booking_dates(
                self.logger, state, payload.booking_date_update
            )
        if payload.command == "update_traveler_composition":
            return apply_update_traveler_composition(
                self.logger, state, payload.traveler_composition_update
            )
        if payload.command == "continue":
            if state.get("stage") == "planning" or state.get("active_agent") == "guide":
                if guide_has_started(state):
                    raise InvalidTripCommandError(
                        "Send a traveler message to continue an existing Guide session."
                    )
                return await apply_guide(self.engine, self.logger, state, "MESSAGE", None, latest_recommendation)
            if state.get("stage") == "matched":
                self._reopen_matching_from_matched(state, context="continue_from_matched")
            if state.get("active_agent") == "meridian" or state.get("stage") in {
                "matching", "recommended"
            }:
                return await apply_meridian(self.engine, self.logger, state, None, latest_recommendation)
            raise InvalidTripCommandError(
                "No agent can continue this trip from its current state."
            )
        if payload.command == "select_destination":
            return select_destination(self.logger, state, payload.option_id or "", latest_recommendation)
        if payload.command == "start_planning":
            if not self._has_planning_destination(state["trip_context"]):
                raise InvalidTripCommandError(
                    "Select or provide a destination before starting planning."
                )
            if state.get("stage") not in {"new", "matched"}:
                raise InvalidTripCommandError(
                    "Planning can only be started from the new or matched stage."
                )
            set_stage(state, "planning", self.logger, context="start_planning")
            state["active_agent"] = "guide"
            return await apply_guide(self.engine, self.logger, state, "MESSAGE", None, latest_recommendation)
        if payload.command == "approve_plan":
            return await apply_guide(self.engine, self.logger, state, "APPROVE_PLAN", None, latest_recommendation)
        if payload.command in {"reopen_destination_revisit", "reopen_destination_fresh"}:
            if not has_pending_reopen_choice(state):
                raise InvalidTripCommandError(
                    "No pending destination-reopen choice to resolve."
                )
            if payload.command == "reopen_destination_revisit":
                return apply_reopen_revisit(self.logger, state)
            return await apply_reopen_fresh(self.engine, self.logger, state, None, latest_recommendation)
        if payload.command == "more_like_this":
            refinement = payload.refinement
            if state.get("stage") == "recommended":
                set_stage(state, "matching", self.logger, context="more_like_this")
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
        message = payload.message or ""
        # Turn zero: no agent owns this trip yet, and the traveler's own
        # Discover-vs-Plan-a-Trip choice decides who gets it — not Scout's
        # intent detection (there's no ambiguity to classify; the UI button
        # already answered the question). The raw message goes to that
        # agent exactly like every later turn's does, so extraction is
        # never a command-handler's job — only the owning agent's.
        if payload.entry_intent == "discover":
            set_stage(state, "matching", self.logger, context="discover_entry")
            state["active_agent"] = "meridian"
            return await apply_meridian(self.engine, self.logger, state, message, latest_recommendation)
        if payload.entry_intent == "known_destination":
            set_stage(state, "planning", self.logger, context="known_destination_entry")
            state["active_agent"] = "guide"
            return await apply_guide(self.engine, self.logger, state, "MESSAGE", message, latest_recommendation)
        if state.get("stage") == "planning" or state.get("active_agent") == "guide":
            return await apply_guide(self.engine, self.logger, state, "MESSAGE", message, latest_recommendation)
        if state.get("stage") == "recommended":
            set_stage(state, "matching", self.logger, context="refinement_traveler_message")
        elif state.get("stage") == "matched":
            self._reopen_matching_from_matched(state, context="matched_reconsider_traveler_message")
        if state.get("active_agent") == "meridian" or state.get("stage") in {
            "matching", "recommended"
        }:
            return await apply_meridian(self.engine, self.logger, state, message, latest_recommendation)
        raise InvalidTripCommandError(
            "No agent can receive this message from the trip's current state."
        )

    def _reopen_matching_from_matched(self, state: dict[str, Any], *, context: str) -> None:
        """A matched trip reconsidering its destination goes straight back
        to Meridian — this used to route through apply_scout's own matcher-
        intent handoff (scout_commands.py), which is no longer reachable
        now that scout_entry is gone; this reproduces its two effects
        (clear the now-obsolete selection, flip stage) deterministically
        instead, since there's no genuine ambiguity left to classify at
        this exact point in the flow."""
        state["selected_option"] = None
        state["trip_context"].pop(DESTINATIONS_KEY, None)
        set_stage(state, "matching", self.logger, context=context)

    @staticmethod
    def _has_planning_destination(trip_context: dict[str, Any]) -> bool:
        # destinations (twm/schemas/trip_context.py) is the one canonical
        # "what's the destination" signal for both entry paths — written
        # directly by select_destination for Discover, extracted by Guide
        # itself for known-destination. No other key means this any more.
        destinations = trip_context.get(DESTINATIONS_KEY)
        return bool(destinations) and any(
            isinstance(item, str) and item.strip() for item in destinations
        )
