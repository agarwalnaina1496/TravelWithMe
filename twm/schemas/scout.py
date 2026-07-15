"""Scout API contracts."""

from typing import Any, Optional

from pydantic import BaseModel, Field

from .common import AgentMeta


class ScoutRequest(BaseModel):
    trip_state: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class ScoutResponse(BaseModel):
    message: Optional[str] = None
    state_delta: dict[str, Any]
    intent: Optional[str] = None
    agent_meta: AgentMeta
