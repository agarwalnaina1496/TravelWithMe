from functools import lru_cache

from .services import get_n8n_engine, n8nEngine


@lru_cache(maxsize=1)
def get_engine() -> n8nEngine:
    return get_n8n_engine()
