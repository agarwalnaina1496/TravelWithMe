"""Shared raw-invocation state used by every agent graph."""

from typing import TypedDict

from langchain_core.messages import BaseMessage


class AgentGraphInput(TypedDict):
    messages: list[BaseMessage]


class AgentGraphState(AgentGraphInput, total=False):
    raw_output: str


class AgentGraphOutput(TypedDict):
    raw_output: str
