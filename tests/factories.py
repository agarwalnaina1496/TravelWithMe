"""Focused test data factories for public API contracts."""

from twm.services.agent_engine import OutputRetryPolicy


def two_attempts() -> OutputRetryPolicy:
    """The retry policy the contract tests assume: one retry, no time limit."""
    return OutputRetryPolicy(max_attempts=2, budget_seconds=10_000)


def traveler_criteria() -> list[dict]:
    return [
        {
            "id": "pace",
            "label": "Relaxed pace",
            "requirement_type": "PREFERENCE",
        }
    ]


def recommendation_option(rank: int = 1) -> dict:
    return {
        "rank": rank,
        "type": "single",
        "name": f"Mountain Haven {rank}",
        "destination_id": f"destination-{rank}",
        "summary": "Supports the requested pace and trip style.",
        "evaluations": [
            {
                "criterion_id": "pace",
                "outcome": "MATCH",
                "conclusion": "The trip can be kept unhurried.",
                "details": [
                    {
                        "type": "bullets",
                        "items": ["Most activities fit within short travel days."],
                    }
                ],
            }
        ],
    }
