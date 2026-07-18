"""Application middleware."""

from .security import SecurityBoundaryMiddleware

__all__ = ["SecurityBoundaryMiddleware"]
