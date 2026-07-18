from fastapi import Request

from .services import AgentEngine


def get_engine(request: Request) -> AgentEngine:
    return request.app.state.agent_engine
