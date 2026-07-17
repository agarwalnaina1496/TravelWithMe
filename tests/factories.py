"""Focused test data factories for public API contracts."""


def traveler_criteria() -> list[dict]:
    return [
        {
            "id": "pace",
            "label": "Relaxed pace",
            "requirement_type": "PREFERENCE",
            "source_context_paths": ["travel_style.pace"],
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
