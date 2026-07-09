from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ScoutRequest(BaseModel):
    trip_state: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class MeridianRequest(BaseModel):
    trip_state: Dict[str, Any] = Field(default_factory=dict)


class ScoutResponse(BaseModel):
    message: Optional[str] = None
    state_delta: Dict[str, Any]
    intent: Optional[str] = None


class MeridianResponse(BaseModel):
    status: str
    state_delta: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None
    generated_at: Optional[str] = None
    version: Optional[str] = None
    trip_type: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None
    relaxation_suggestions: Optional[List[str]] = None
