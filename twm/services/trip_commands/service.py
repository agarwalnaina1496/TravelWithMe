"""Backend-owned TripState command orchestration and dispatch."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...persistence.contracts import RecommendationRecord, TripCommandRecord, TripOwner, TripRecord, TripRepository
from ...schemas.trips import (
    TripCommandRequest,
    TripCommandResponse,
    TripFirstMessageRequest,
    TripRecommendationsResponse,
    TripResponse,
)
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from .errors import IdempotencyConflictError, InvalidTripCommandError
from .handlers import COMMAND_HANDLERS, CommandContext
from .state import (
    canonical_state,
    shape_command_trip_state,
    snapshot_touchable_branches,
    touched_branches,
)


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
        recommendation = None
        if new_recommendation is not None:
            # TWM-217: the turn produced a matcher round — return it inline
            # (the UI consumes this in TWM-220, saving a second fetch) and
            # store it so an idempotency replay returns it identically. The
            # round is immutable once archived; created_at is stamped now,
            # matching the row's own DEFAULT now().
            recommendation = TripRecommendationsResponse.model_validate(
                {**new_recommendation, "created_at": datetime.now(timezone.utc)}
            )
        response_without_trip = {
            "message": result["message"],
            "agent_meta": result["agent_meta"],
            "recommendation": recommendation.model_dump(mode="json") if recommendation else None,
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
            recommendation=recommendation,
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
        if result.pop("new_recommendation", None) is not None:
            self.logger.warning(
                "First-message agent turn produced an archive-table "
                "result before any trip existed to archive it against; "
                "discarding it — this should be unreachable once the "
                "Meridian/Guide gates hold.",
                event="be.trip.first_message.unexpected_archive_result",
                source="application",
                entry_intent=payload.entry_intent,
                leaked_key="new_recommendation",
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
        # TWM-223: dispatch is a registry lookup, not an if-chain. Each
        # handler owns its precondition and its application; this method
        # never changes when a command is added. See handlers.py.
        handler = COMMAND_HANDLERS[payload.command]
        if state["planner_state"].get("frozen_plan") and not handler.post_freeze_ok:
            raise InvalidTripCommandError(
                "The approved plan is frozen and cannot be changed."
            )
        ctx = CommandContext(
            state=state,
            payload=payload,
            engine=self.engine,
            logger=self.logger,
            latest_recommendation=latest_recommendation,
        )
        handler.precondition(ctx)
        return await handler.apply(ctx)
