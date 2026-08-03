"""Contracts shared by multiple agents."""

from typing import Literal

from pydantic import BaseModel


class AgentMeta(BaseModel):
    agent: Literal["scout", "meridian", "guide"]
    prompt_version: str
