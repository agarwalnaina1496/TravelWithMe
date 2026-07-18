"""Reusable fake structured model for graph tests."""

from typing import Any

from twm.prompts import PromptRelease
from twm.services import LangGraphAgentEngine, LangGraphRuntime


class FakeStructuredModel:
    def __init__(self, schema: type, outputs: list[Any], calls: list[list[Any]]) -> None:
        self.schema = schema
        self.outputs = outputs
        self.calls = calls

    async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
        self.calls.append(messages)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        if isinstance(output, dict) and "parsing_error" in output:
            return output
        try:
            parsed = self.schema.model_validate(output)
        except Exception as exc:
            return {"raw": None, "parsed": None, "parsing_error": exc}
        return {"raw": None, "parsed": parsed, "parsing_error": None}


class FakeChatModel:
    def __init__(self, outputs: dict[str, list[Any]]) -> None:
        self.outputs = outputs
        self.calls: dict[str, list[list[Any]]] = {}
        self.structured_options: dict[str, dict[str, Any]] = {}

    def with_structured_output(self, schema: type, **kwargs: Any) -> FakeStructuredModel:
        name = schema.__name__
        self.calls[name] = []
        self.structured_options[name] = kwargs
        return FakeStructuredModel(schema, self.outputs[name], self.calls[name])


def prompt_release(agent: str) -> PromptRelease:
    return PromptRelease(agent, f"{agent}-version", f"{agent} system prompt")


def make_langgraph_engine(
    outputs: dict[str, list[Any]],
) -> LangGraphAgentEngine:
    outputs.setdefault("ScoutModelOutput", [])
    outputs.setdefault("MeridianModelOutput", [])
    runtime = LangGraphRuntime(model=FakeChatModel(outputs))
    return LangGraphAgentEngine(runtime=runtime)
