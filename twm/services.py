from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Dict, Optional, Protocol

import httpx

from .prompts import PromptRelease, load_prompt_release
from .shared.properties import property_loader


@dataclass(frozen=True)
class AgentExecution:
    response: Dict[str, Any]
    prompt_release: PromptRelease


class AgentEngine(Protocol):
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> AgentExecution:
        ...

    def meridian(self, trip_state: Dict[str, Any]) -> AgentExecution:
        ...


class N8NAgentEngine:
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> AgentExecution:
        release = load_prompt_release("scout")
        response = self._forward(
            "n8n_scout_webhook_url",
            {
                "prompt": release.content,
                "trip_state": trip_state,
                "message": message,
            },
        )
        return AgentExecution(response=response, prompt_release=release)

    def meridian(self, trip_state: Dict[str, Any]) -> AgentExecution:
        release = load_prompt_release("meridian")
        response = self._forward(
            "n8n_meridian_webhook_url",
            {
                "prompt": release.content,
                "trip_state": trip_state,
            },
        )
        return AgentExecution(response=response, prompt_release=release)

    def _forward(self, property_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            url = property_loader.get_string_property(property_key)
        except Exception:
            return {
                "status": "HARD_FAIL",
                "message": f"{property_key} is not configured.",
                "state_delta": {},
                "options": [],
            }

        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()


def get_agent_engine() -> AgentEngine:
    engine_name = property_loader.get_string_property_with_default(
        "agent_engine", "n8n"
    ).lower()

    if engine_name == "n8n":
        return N8NAgentEngine()

    raise ValueError(f"Unsupported agent_engine: {engine_name}")


def _get_trip_context_value(trip_state: Dict[str, Any], key: str) -> Any:
    trip_context = trip_state.get("trip_context") if isinstance(trip_state, dict) else None
    if isinstance(trip_context, dict) and key in trip_context:
        return trip_context[key]
    return trip_state.get(key) if isinstance(trip_state, dict) else None


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _extract_context_from_message(message: Optional[str], trip_state: Dict[str, Any]) -> Dict[str, Any]:
    prepared_state = deepcopy(trip_state or {})
    trip_context = prepared_state.setdefault("trip_context", {})
    if not isinstance(trip_context, dict):
        trip_context = {}
        prepared_state["trip_context"] = trip_context

    if not message:
        return prepared_state

    normalized = message.lower()

    if not _has_value(_get_trip_context_value(prepared_state, "budget")):
        budget_match = re.search(
            r"(?:budget|under|around|about|up to|within)\s*(?:\$|usd|inr|rupees?|eur|gbp)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)",
            message,
            flags=re.IGNORECASE,
        )
        if budget_match:
            trip_context["budget"] = budget_match.group(1)

    if not _has_value(_get_trip_context_value(prepared_state, "travel_dates")):
        date_match = re.search(
            r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|(?:january|february|march|april|may|june|july|august|september|october|november|december))\b",
            message,
            flags=re.IGNORECASE,
        )
        if date_match:
            trip_context["travel_dates"] = date_match.group(0)
        elif re.search(r"\b(?:for|for a)\s+\d+\s+(?:day|days|week|weeks|month|months)\b", normalized):
            trip_context["travel_dates"] = "duration inferred from message"

    if not _has_value(_get_trip_context_value(prepared_state, "travelers")):
        traveler_match = re.search(r"\b(?:solo|couple|family|friends|for\s+\d+\s+(?:people|travelers|adults|kids))\b", normalized)
        if traveler_match:
            trip_context["travelers"] = traveler_match.group(0)

    if not _has_value(_get_trip_context_value(prepared_state, "trip_type")) and not _has_value(_get_trip_context_value(prepared_state, "interests")):
        if any(keyword in normalized for keyword in ["beach", "adventure", "culture", "food", "relax", "shopping", "hiking", "nature", "city"]):
            trip_context["trip_type"] = "interest-driven"

    return prepared_state


def build_conversation_clarification(
    trip_state: Dict[str, Any], message: Optional[str]
) -> Optional[Dict[str, Any]]:
    prepared_state = _extract_context_from_message(message, trip_state)
    trip_context = prepared_state.get("trip_context") or {}

    missing_fields = []
    for field in ["destination", "budget", "travel_dates"]:
        if not _has_value(_get_trip_context_value(prepared_state, field)):
            missing_fields.append(field)

    if not missing_fields:
        return None

    first_missing = missing_fields[0]
    if first_missing == "destination":
        question = (
            "To help me make a useful recommendation, what destination are you considering, and "
            "what budget and travel dates do you have in mind?"
        )
    elif first_missing == "budget":
        question = "What budget do you have in mind for the trip?"
    elif first_missing == "travel_dates":
        question = "When are you planning to travel, and for how long?"
    elif first_missing == "travelers":
        question = "How many travelers are going?"
    else:
        question = "What kind of trip experience are you looking for?"

    return {
        "status": "NEEDS_CLARIFICATION",
        "message": question,
        "state_delta": {
            "trip_state": prepared_state,
            "matcher_state": {
                "conversation_context": {
                    "awaiting": first_missing,
                }
            },
        },
        "options": [],
        "trip_state": prepared_state,
    }
