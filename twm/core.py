from functools import lru_cache

from .services import AgentEngine, get_agent_engine


@lru_cache(maxsize=1)
def get_engine() -> AgentEngine:
    return get_agent_engine()
