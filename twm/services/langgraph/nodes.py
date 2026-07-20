"""Single provider-neutral invocation node shared by every graph."""

from typing import Any

from .state import AgentGraphState


class InvokeModelNode:
    """Invoke one dependency-injected chat model and return raw text."""

    def __init__(self, model: Any) -> None:
        self._model = model

    async def __call__(self, state: AgentGraphState) -> dict[str, str]:
        response = await self._model.ainvoke(state["messages"])
        content = getattr(response, "content", None)
        if not isinstance(content, str):
            raise ValueError("model response content must be text")
        return {"raw_output": content}
