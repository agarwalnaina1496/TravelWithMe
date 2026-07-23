"""Reusable provider-neutral chat-model fake for LangGraph tests."""

import json
from typing import Any

from langchain_core.messages import AIMessage


class FakeChatModel:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[list[Any]] = []
        self.schemas: list[dict[str, Any]] = []

    def with_structured_output(
        self, schema: dict[str, Any], *, include_raw: bool
    ) -> "FakeStructuredModel":
        assert include_raw is True
        self.schemas.append(schema)
        return FakeStructuredModel(self)


class FakeStructuredModel:
    def __init__(self, model: FakeChatModel) -> None:
        self.model = model

    async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
        self.model.calls.append(messages)
        output = self.model.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        response = output if isinstance(output, AIMessage) else AIMessage(content=output)
        try:
            parsed = json.loads(response.content)
            parsing_error = None
        except (TypeError, json.JSONDecodeError) as error:
            parsed = None
            parsing_error = error
        return {
            "raw": response,
            "parsed": parsed,
            "parsing_error": parsing_error,
        }
