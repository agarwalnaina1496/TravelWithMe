"""Shared raw-invocation state used by every agent graph."""

from typing import Any, TypedDict

from langchain_core.messages import BaseMessage


class AgentGraphInput(TypedDict):
    messages: list[BaseMessage]
    output_schema: dict[str, Any]


class AgentGraphState(AgentGraphInput, total=False):
    raw_output: str
    provider_metadata: dict[str, Any]


class AgentGraphOutput(TypedDict):
    raw_output: str
    provider_metadata: dict[str, Any]
