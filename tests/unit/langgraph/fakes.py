"""Reusable provider-neutral chat-model fake for LangGraph tests."""

from typing import Any

from langchain_core.messages import AIMessage


class FakeChatModel:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(messages)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return AIMessage(content=output)
