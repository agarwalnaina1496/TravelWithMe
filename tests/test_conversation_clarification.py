import unittest

from twm.services import build_conversation_clarification


class ConversationClarificationTests(unittest.TestCase):
    def test_initial_message_requests_missing_trip_details(self) -> None:
        result = build_conversation_clarification({}, "I want to plan a trip")

        self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
        self.assertIn("?", result["message"])
        self.assertIn("destination", result["message"].lower())
        self.assertEqual(
            result["state_delta"]["matcher_state"]["conversation_context"]["awaiting"],
            "destination",
        )

    def test_budget_and_dates_are_carried_forward_without_extra_questions(self) -> None:
        trip_state = {
            "trip_context": {
                "destination": "Goa",
                "budget": "$3000",
                "travel_dates": "2026-08-10 to 2026-08-14",
            }
        }

        result = build_conversation_clarification(trip_state, "I want a relaxing beach trip")

        self.assertIsNone(result)
