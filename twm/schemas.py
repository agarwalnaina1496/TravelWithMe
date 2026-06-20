from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TripState(BaseModel):
    class Config:
        extra = "allow"


class ScoutRequest(BaseModel):
    trip_state: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class MeridianRequest(BaseModel):
    trip_context: Dict[str, Any] = Field(default_factory=dict)


class ScoutResponse(BaseModel):
    message: str
    state_delta: Dict[str, Any]


class MeridianResponse(BaseModel):
    status: str
    trip_type: Optional[str] = None
    budget_basis: Optional[Dict[str, Any]] = None
    options: Optional[List[Dict[str, Any]]] = None
    final_recommendation: Optional[Dict[str, Any]] = None
    refinement_hooks: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    eliminating_constraints: Optional[List[str]] = None
    relaxation_suggestions: Optional[List[str]] = None
    surviving_destinations: Optional[List[str]] = None
