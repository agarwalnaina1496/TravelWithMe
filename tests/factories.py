"""Focused test data factories for public API contracts."""


def recommendation_option(rank: int = 1) -> dict:
    return {
        "rank": rank,
        "type": "single",
        "name": f"Mountain Haven {rank}",
        "destination_id": f"destination-{rank}",
        "verdict": "Strong overall fit",
        "summary": "Supports the requested pace and trip style.",
        "criteria": [
            {
                "id": "pace",
                "label": "Relaxed pace",
                "requirement_type": "PREFERENCE",
                "outcome": "MATCH",
                "summary": "The trip can be kept unhurried.",
                "details": [
                    {
                        "type": "note",
                        "text": "Most activities fit within short travel days.",
                    }
                ],
            }
        ],
    }
