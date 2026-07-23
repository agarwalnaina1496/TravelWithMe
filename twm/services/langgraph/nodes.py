"""Single provider-neutral invocation node shared by every graph."""

from collections.abc import Mapping
from typing import Any

from .state import AgentGraphState


class InvokeModelNode:
    """Invoke one dependency-injected chat model and return raw text."""

    def __init__(self, model: Any) -> None:
        self._model = model

    async def __call__(self, state: AgentGraphState) -> dict[str, Any]:
        response = await self._model.ainvoke(state["messages"])
        content = getattr(response, "content", "")
        raw_output = content if isinstance(content, str) else ""
        return {
            "raw_output": raw_output,
            "provider_metadata": _extract_provider_metadata(response),
        }


def _extract_provider_metadata(response: Any) -> dict[str, str | int | float]:
    """Normalize only non-content metadata that the provider actually returned."""

    response_metadata = _mapping(getattr(response, "response_metadata", None))
    usage_metadata = _mapping(getattr(response, "usage_metadata", None))
    token_usage = _mapping(response_metadata.get("token_usage"))
    output_details = _mapping(usage_metadata.get("output_token_details"))
    completion_details = _mapping(token_usage.get("completion_tokens_details"))

    values = {
        "finish_reason": response_metadata.get("finish_reason"),
        "input_tokens": usage_metadata.get(
            "input_tokens", token_usage.get("prompt_tokens")
        ),
        "output_tokens": usage_metadata.get(
            "output_tokens", token_usage.get("completion_tokens")
        ),
        "total_tokens": usage_metadata.get(
            "total_tokens", token_usage.get("total_tokens")
        ),
        "reasoning_tokens": output_details.get(
            "reasoning", completion_details.get("reasoning_tokens")
        ),
        "queue_time_ms": _seconds_to_ms(token_usage.get("queue_time")),
        "model_time_ms": _model_time_ms(token_usage),
        "provider_total_time_ms": _seconds_to_ms(token_usage.get("total_time")),
        "provider_attempts": 1,
    }
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _seconds_to_ms(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(float(value) * 1000, 3)


def _model_time_ms(token_usage: Mapping[str, Any]) -> float | None:
    prompt_time = token_usage.get("prompt_time")
    completion_time = token_usage.get("completion_time")
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (prompt_time, completion_time)
    ):
        return round((float(prompt_time) + float(completion_time)) * 1000, 3)

    total_time = token_usage.get("total_time")
    queue_time = token_usage.get("queue_time")
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (total_time, queue_time)
    ):
        return round((float(total_time) - float(queue_time)) * 1000, 3)
    return _seconds_to_ms(completion_time)
