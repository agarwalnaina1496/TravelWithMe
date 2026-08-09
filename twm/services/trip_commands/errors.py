"""Errors raised while applying Backend-owned TripState commands."""


class IdempotencyConflictError(ValueError):
    pass


class InvalidTripCommandError(ValueError):
    pass
