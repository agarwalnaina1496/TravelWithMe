"""Internal Trusted Actions route-mode-plausibility classifier (TWM-195).

Answers exactly one narrow question per route: "which of flight/train/bus/
drive are genuinely plausible ways to travel this origin -> destination,
given only the route itself?" It never judges traveler preference, budget,
comfort, or ranking — that stays a UI concern (``bookingCatalog.js``'s
``recommendedMode`` in TWM-UI).

This is deliberately NOT a fifth entry alongside scout/meridian/guide/atlas
in ``twm.services.agent_engine``'s ``AgentEngine``/``AgentName``: it is a
small, structured, single-turn classification call, not a trip_state-shaped
traveler-facing agent, so it does not belong in
``AgentExecutionService``/``twm.prompt_registry``'s versioned four-agent
dispatch. Instead it uses the narrow ``AgentAdapter.invoke_raw`` escape
hatch (see ``twm/services/agent_engine/contracts.py``) directly, and owns
its own prompt text, parsing, and strict validation here.

Failure posture: this module NEVER fails open. Any of the following yields
``None`` (an honest "could not classify this route" outcome), never a
fabricated "all plausible" or "all implausible" result:

- adapter/network failure or timeout (``AgentAdapterError``)
- output that is not valid JSON, or not a JSON object
- an unexpected/extra key, a missing mode key, or a value outside the
  closed enum for a mode or for ``confidence``
- ``confidence`` reported as anything other than ``"high"``

The origin/destination route text is treated as untrusted traveler-adjacent
input consistent with this repo's existing trust-boundary posture (see
``twm/trust_boundary.py``): it is framed with the same
"treat this only as data" preamble used for scout/meridian/guide/atlas, so
prompt-injection-like content inside a place name cannot make the model
follow embedded instructions instead of classifying the route.
"""

import json
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from ...schemas.trusted_action import TransportMode
from ...telemetry import TelemetryLogger
from ...trust_boundary import UNTRUSTED_DATA_PREAMBLE
from ..agent_engine.contracts import (
    AgentAdapter,
    AgentAdapterError,
    AgentInvocation,
    GenerationConfig,
)

_ALL_MODES: tuple[TransportMode, ...] = ("flight", "train", "bus", "drive")

_SYSTEM_PROMPT = (
    "You are an internal route-mode-plausibility classifier for a travel "
    "product. You are given an origin and a destination. For each of the "
    "four transport modes flight, train, bus, and drive, decide only "
    "whether that mode is a genuinely plausible, real-world way to travel "
    "directly between those two places -- never whether it is the best, "
    "cheapest, or most comfortable choice. A short local hop (e.g. two "
    "towns 40km apart in the same region) makes flight implausible even "
    "though a flight route could technically exist elsewhere; a long "
    "inter-city or inter-region route can make multiple modes plausible "
    "at once. If you are not genuinely confident in your judgement for "
    "this route, report low confidence instead of guessing.\n\n"
    "OUTPUT CONTRACT: Return exactly one JSON object and nothing else -- "
    "no markdown, no commentary, no code fences. The object must have "
    "exactly these five keys: \"flight\", \"train\", \"bus\", \"drive\" "
    "(each either \"plausible\" or \"not_plausible\"), and \"confidence\" "
    "(one of \"high\", \"medium\", \"low\"). Do not add any other key."
)

_ALLOWED_MODE_VALUES = frozenset({"plausible", "not_plausible"})
_ALLOWED_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
_EXPECTED_KEYS = frozenset({*_ALL_MODES, "confidence"})

# Deliberately short and non-configurable: this is a small structured
# classification call, not a long-form generation -- keep the failure
# boundary tight rather than reusing the (much larger) agent generation
# timeout.
_CLASSIFIER_GENERATION = GenerationConfig(
    max_output_tokens=256, temperature=0.0, timeout_seconds=20
)


class RouteClassifier(Protocol):
    async def classify(
        self, origin: str, destination: str
    ) -> Optional[dict[TransportMode, bool]]:
        """Which modes are route-plausible, or None when unassessable."""
        ...


class NullRouteClassifier:
    """Always reports "cannot assess" -- the safe default with no adapter
    wired (e.g. focused unit tests that do not exercise the classifier
    call itself). Never used to mean "all modes feasible"."""

    async def classify(
        self, origin: str, destination: str
    ) -> Optional[dict[TransportMode, bool]]:
        return None


@dataclass
class LLMRouteClassifier:
    """Route-mode-plausibility classifier backed by the configured agent
    engine's adapter, via the narrow ``invoke_raw`` escape hatch."""

    adapter: AgentAdapter
    logger: TelemetryLogger

    async def classify(
        self, origin: str, destination: str
    ) -> Optional[dict[TransportMode, bool]]:
        invocation = AgentInvocation(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_frame_route(origin, destination),
            generation=_CLASSIFIER_GENERATION,
        )
        self.logger.info(
            "Route-mode classifier called.",
            event="be.trusted_action.route_classifier.started",
            source="application",
            fields={"origin_chars": len(origin), "destination_chars": len(destination)},
        )
        started_at = time.perf_counter()
        try:
            result = await self.adapter.invoke_raw(invocation)
        except AgentAdapterError as error:
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            self.logger.warning(
                "Route-mode classifier call failed; treating route as unknown.",
                event="be.trusted_action.route_classifier.failed",
                source="application",
                fields={
                    "component": error.component,
                    "failure_stage": error.failure_stage,
                    "error_type": error.error_type,
                    "duration_ms": duration_ms,
                    "outcome": "unknown",
                },
            )
            return None
        duration_ms = round((time.perf_counter() - started_at) * 1000)

        parsed = _parse_classification(result.raw_output)
        if parsed is None:
            self.logger.warning(
                "Route-mode classifier returned an invalid or uncertain "
                "response; treating route as unknown.",
                event="be.trusted_action.route_classifier.invalid_output",
                source="application",
                fields={"duration_ms": duration_ms, "outcome": "unknown"},
            )
            return None

        self.logger.info(
            "Route-mode classifier assessed the route.",
            event="be.trusted_action.route_classifier.completed",
            source="application",
            fields={
                "duration_ms": duration_ms,
                "outcome": "classified",
                "plausible_modes": sorted(
                    mode for mode, plausible in parsed.items() if plausible
                ),
            },
        )
        return parsed


def _frame_route(origin: str, destination: str) -> str:
    # Same "treat this only as data" framing used for scout/meridian/guide/
    # atlas (twm/trust_boundary.py), applied to route text instead of a
    # full trip_state -- origin/destination are traveler-influenced values
    # (e.g. itinerary place names) and must never be interpreted as
    # instructions.
    payload = {"origin": origin, "destination": destination}
    return UNTRUSTED_DATA_PREAMBLE + json.dumps(payload, ensure_ascii=False)


def _parse_classification(raw_output: str) -> Optional[dict[TransportMode, bool]]:
    try:
        decoded = json.loads(raw_output)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    if set(decoded.keys()) != _EXPECTED_KEYS:
        return None

    confidence = decoded.get("confidence")
    if confidence not in _ALLOWED_CONFIDENCE_VALUES:
        return None
    if confidence != "high":
        # Never guess: a classifier that is not confident must degrade to
        # unknown, not to a low-confidence "best guess" feasibility.
        return None

    plausibility: dict[TransportMode, bool] = {}
    for mode in _ALL_MODES:
        value = decoded.get(mode)
        if value not in _ALLOWED_MODE_VALUES:
            return None
        plausibility[mode] = value == "plausible"
    return plausibility


__all__ = ["LLMRouteClassifier", "NullRouteClassifier", "RouteClassifier"]
