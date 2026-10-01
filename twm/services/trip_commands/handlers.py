"""TWM-223: the trip-command dispatch registry.

``TripCommandService._apply`` is a registry lookup, not an ``if`` chain:
``COMMAND_HANDLERS[payload.command]`` -> a handler that owns its own
precondition check and its ``apply``. Adding a command means adding a
handler + a registry entry; ``_apply`` never changes.

The handler bodies are the existing ``apply_*`` module functions and the
routing that used to live inline in ``_apply`` — relocated verbatim, not
rewritten. This is the reference implementation the "dispatch is a registry,
not an if-chain" rule in ``AGENTS.md`` points at.
"""

from dataclasses import dataclass
from typing import Any, ClassVar

from ...persistence.contracts import RecommendationRecord
from ...schemas.trip_context import DESTINATIONS_KEY
from ...schemas.trips import TripCommandName, TripCommandRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from .atlas_commands import apply_atlas
from .booking_commands import (
    apply_clear_search_pref,
    apply_set_party,
    apply_set_search_pref,
)
from .errors import InvalidTripCommandError
from .matcher_commands import apply_meridian, select_destination
from .planner_commands import (
    apply_guide,
    apply_remove_place,
    apply_reopen_fresh,
    apply_reopen_revisit,
    guide_has_started,
    has_pending_reopen_choice,
)
from .state import set_stage


@dataclass
class CommandContext:
    """Everything a handler needs to run one command turn."""

    state: dict[str, Any]
    payload: TripCommandRequest
    engine: AgentEngine
    logger: TelemetryLogger
    latest_recommendation: RecommendationRecord | None


class CommandHandler:
    """One command's precondition + application.

    ``post_freeze_ok`` marks the booking-setup commands that stay valid
    after the plan is frozen (they never regenerate the itinerary).
    """

    post_freeze_ok: ClassVar[bool] = False

    def precondition(self, ctx: CommandContext) -> None:
        """Raise ``InvalidTripCommandError`` if this command cannot run from
        the trip's current state. Default: always allowed."""

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        raise NotImplementedError


def reopen_matching_from_matched(
    state: dict[str, Any], logger: TelemetryLogger, *, context: str
) -> None:
    """A matched trip reconsidering its destination goes straight back to
    Meridian — this used to route through apply_scout's own matcher-intent
    handoff (scout_commands.py), which is no longer reachable now that
    scout_entry is gone; this reproduces its two effects (clear the
    now-obsolete selection, flip stage) deterministically instead, since
    there's no genuine ambiguity left to classify at this exact point in
    the flow."""

    state["selected_option"] = None
    state["trip_context"].pop(DESTINATIONS_KEY, None)
    set_stage(state, "matching", logger, context=context)


def _has_planning_destination(trip_context: dict[str, Any]) -> bool:
    # destinations (twm/schemas/trip_context.py) is the one canonical
    # "what's the destination" signal for both entry paths — written
    # directly by select_destination for Discover, extracted by Guide
    # itself for known-destination. No other key means this any more.
    destinations = trip_context.get(DESTINATIONS_KEY)
    return bool(destinations) and any(
        isinstance(item, str) and item.strip() for item in destinations
    )


class StartItineraryHandler(CommandHandler):
    post_freeze_ok = True

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return await apply_atlas(ctx.engine, ctx.logger, ctx.state)


class SetPartyHandler(CommandHandler):
    post_freeze_ok = True

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return apply_set_party(ctx.logger, ctx.state, ctx.payload.party_update)


class SetSearchPrefHandler(CommandHandler):
    post_freeze_ok = True

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return apply_set_search_pref(
            ctx.logger, ctx.state, ctx.payload.search_pref_update
        )


class ClearSearchPrefHandler(CommandHandler):
    post_freeze_ok = True

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return apply_clear_search_pref(
            ctx.logger, ctx.state, ctx.payload.search_pref_clear
        )


class ContinueHandler(CommandHandler):
    """``continue`` is a kickoff for a matcher round that hasn't produced a
    result yet -- it never mutates a trip that already has one. A matched or
    recommended trip with an existing round has nothing to continue, so this
    is a no-op rather than a redundant Meridian call: re-invoking Meridian
    here would regenerate the same result and then collide with the
    "recommended" stage it already set (`set_stage` rejects a same-stage
    transition), and for a matched trip it would mean silently discarding
    the traveler's chosen destination -- a side effect `continue` has no
    business causing on its own. Deliberately reconsidering a matched
    destination is `unselect_destination`'s job, or a real traveler message
    (`TravelerMessageHandler`'s own matched-stage reopen), never a bare,
    possibly-stray `continue`."""

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        state, engine, logger = ctx.state, ctx.engine, ctx.logger
        latest = ctx.latest_recommendation
        if state.get("stage") == "planning" or state.get("active_agent") == "guide":
            if guide_has_started(state):
                raise InvalidTripCommandError(
                    "Send a traveler message to continue an existing Guide session."
                )
            return await apply_guide(engine, logger, state, "MESSAGE", None, latest)
        if state.get("stage") in {"recommended", "matched"} and latest is not None:
            return {"message": None, "agent_meta": None}
        if state.get("active_agent") == "meridian" or state.get("stage") in {
            "matching",
            "recommended",
        }:
            return await apply_meridian(engine, logger, state, None, latest)
        raise InvalidTripCommandError(
            "No agent can continue this trip from its current state."
        )


class SelectDestinationHandler(CommandHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return select_destination(
            ctx.logger,
            ctx.state,
            ctx.payload.option_id or "",
            ctx.latest_recommendation,
        )


class UnselectDestinationHandler(CommandHandler):
    """TWM-234: lets the traveler reconsider a matched destination without
    starting to plan it -- the Destinations page's "Compare other
    destinations" action. Goes back to `matching`, never a direct
    `matched` -> `matched` re-selection (illegal per `STAGE_TRANSITIONS`);
    the already-fetched recommendation round stays available for the UI to
    show immediately, so this never re-invokes Meridian itself."""

    def precondition(self, ctx: CommandContext) -> None:
        if ctx.state.get("stage") != "matched":
            raise InvalidTripCommandError(
                "Only a matched destination can be reconsidered."
            )

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        reopen_matching_from_matched(
            ctx.state, ctx.logger, context="unselect_destination"
        )
        return {"message": "Let's look at other destinations.", "agent_meta": None}


class StartPlanningHandler(CommandHandler):
    def precondition(self, ctx: CommandContext) -> None:
        if not _has_planning_destination(ctx.state["trip_context"]):
            raise InvalidTripCommandError(
                "Select or provide a destination before starting planning."
            )
        if ctx.state.get("stage") not in {"new", "matched"}:
            raise InvalidTripCommandError(
                "Planning can only be started from the new or matched stage."
            )

    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        set_stage(ctx.state, "planning", ctx.logger, context="start_planning")
        ctx.state["active_agent"] = "guide"
        return await apply_guide(
            ctx.engine, ctx.logger, ctx.state, "MESSAGE", None, ctx.latest_recommendation
        )


class ApprovePlanHandler(CommandHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return await apply_guide(
            ctx.engine,
            ctx.logger,
            ctx.state,
            "APPROVE_PLAN",
            None,
            ctx.latest_recommendation,
        )


class RemovePlaceHandler(CommandHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return apply_remove_place(
            ctx.logger,
            ctx.state,
            ctx.payload.place_name or "",
            ctx.payload.day_number,
        )


class _ReopenDestinationHandler(CommandHandler):
    def precondition(self, ctx: CommandContext) -> None:
        if not has_pending_reopen_choice(ctx.state):
            raise InvalidTripCommandError(
                "No pending destination-reopen choice to resolve."
            )


class ReopenDestinationRevisitHandler(_ReopenDestinationHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return apply_reopen_revisit(ctx.logger, ctx.state)


class ReopenDestinationFreshHandler(_ReopenDestinationHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        return await apply_reopen_fresh(
            ctx.engine, ctx.logger, ctx.state, None, ctx.latest_recommendation
        )


class MoreLikeThisHandler(CommandHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        refinement = ctx.payload.refinement
        if ctx.state.get("stage") == "recommended":
            set_stage(ctx.state, "matching", ctx.logger, context="more_like_this")
        return await apply_meridian(
            ctx.engine,
            ctx.logger,
            ctx.state,
            refinement.instructions if refinement else None,
            ctx.latest_recommendation,
            refinement=refinement.model_dump(mode="json", exclude_none=True)
            if refinement
            else None,
        )


class TravelerMessageHandler(CommandHandler):
    async def apply(self, ctx: CommandContext) -> dict[str, Any]:
        state, payload, engine, logger = ctx.state, ctx.payload, ctx.engine, ctx.logger
        latest = ctx.latest_recommendation
        message = payload.message or ""
        # Turn zero: no agent owns this trip yet, and the traveler's own
        # Discover-vs-Plan-a-Trip choice decides who gets it — not Scout's
        # intent detection (there's no ambiguity to classify; the UI button
        # already answered the question). The raw message goes to that agent
        # exactly like every later turn's does, so extraction is never a
        # command-handler's job — only the owning agent's.
        if payload.entry_intent == "discover":
            set_stage(state, "matching", logger, context="discover_entry")
            state["active_agent"] = "meridian"
            return await apply_meridian(engine, logger, state, message, latest)
        if payload.entry_intent == "known_destination":
            set_stage(state, "planning", logger, context="known_destination_entry")
            state["active_agent"] = "guide"
            return await apply_guide(engine, logger, state, "MESSAGE", message, latest)
        if state.get("stage") == "planning" or state.get("active_agent") == "guide":
            return await apply_guide(engine, logger, state, "MESSAGE", message, latest)
        if state.get("stage") == "recommended":
            set_stage(
                state, "matching", logger, context="refinement_traveler_message"
            )
        elif state.get("stage") == "matched":
            reopen_matching_from_matched(
                state, logger, context="matched_reconsider_traveler_message"
            )
        if state.get("active_agent") == "meridian" or state.get("stage") in {
            "matching",
            "recommended",
        }:
            return await apply_meridian(engine, logger, state, message, latest)
        raise InvalidTripCommandError(
            "No agent can receive this message from the trip's current state."
        )


COMMAND_HANDLERS: dict[TripCommandName, CommandHandler] = {
    "traveler_message": TravelerMessageHandler(),
    "continue": ContinueHandler(),
    "select_destination": SelectDestinationHandler(),
    "unselect_destination": UnselectDestinationHandler(),
    "start_planning": StartPlanningHandler(),
    "approve_plan": ApprovePlanHandler(),
    "more_like_this": MoreLikeThisHandler(),
    "remove_place": RemovePlaceHandler(),
    "reopen_destination_revisit": ReopenDestinationRevisitHandler(),
    "reopen_destination_fresh": ReopenDestinationFreshHandler(),
    "start_itinerary": StartItineraryHandler(),
    "set_party": SetPartyHandler(),
    "set_search_pref": SetSearchPrefHandler(),
    "clear_search_pref": ClearSearchPrefHandler(),
}
