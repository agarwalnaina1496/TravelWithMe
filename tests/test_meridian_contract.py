import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from twm.prompts import PromptRelease
from twm.routers.trip_matcher import meridian as meridian_route
from twm.schemas import AgentMeta, MeridianRequest, MeridianResponse, ScoutRequest
from twm.services import AgentExecution, N8NAgentEngine


class MeridianRequestContractTests(unittest.TestCase):
    def test_accepts_approved_phase_slice_and_message(self) -> None:
        request = MeridianRequest(
            trip_state={
                "trip_context": {"destination_scope": "mountains"},
                "advisor_state": {
                    "conversation_context": {
                        "last_advisor_message": "I compared the broad options."
                    }
                },
                "matcher_state": {
                    "conversation_context": {
                        "last_meridian_message": None,
                        "awaiting": None,
                    }
                },
            },
            message="Help me narrow those down.",
        )

        self.assertEqual(request.message, "Help me narrow those down.")
        self.assertEqual(
            request.trip_state.advisor_state.conversation_context.last_advisor_message,
            "I compared the broad options.",
        )

    def test_allows_invocation_without_a_new_message(self) -> None:
        request = MeridianRequest(
            trip_state={
                "trip_context": {},
                "advisor_state": {"conversation_context": {}},
                "matcher_state": {},
            }
        )

        self.assertIsNone(request.message)

    def test_rejects_full_ui_state_instead_of_phase_slice(self) -> None:
        with self.assertRaises(ValidationError):
            MeridianRequest(
                trip_state={
                    "stage": "matching",
                    "trip_context": {},
                    "advisor_state": {"conversation_context": {}},
                    "matcher_state": {},
                }
            )

    def test_scout_request_contract_remains_unchanged(self) -> None:
        request = ScoutRequest(
            trip_state={
                "stage": "new",
                "trip_context": {},
                "advisor_state": {},
            },
            message="I need travel advice.",
        )

        self.assertEqual(request.message, "I need travel advice.")
        self.assertEqual(request.trip_state["stage"], "new")


class MeridianForwardingTests(unittest.TestCase):
    def test_engine_forwards_trip_state_and_message_without_transformation(self) -> None:
        trip_state = {
            "trip_context": {"destination_scope": "mountains"},
            "advisor_state": {
                "conversation_context": {"last_advisor_message": "Prior advice"}
            },
            "matcher_state": {"conversation_context": {"awaiting": "budget"}},
        }
        engine = N8NAgentEngine()

        with patch.object(engine, "_forward", return_value={}) as forward:
            engine.meridian(trip_state, "Around 80,000.")

        payload = forward.call_args.args[1]
        self.assertEqual(payload["trip_state"], trip_state)
        self.assertEqual(payload["message"], "Around 80,000.")

    def test_existing_n8n_workflow_maps_message(self) -> None:
        workflow_path = Path(__file__).parents[1] / "n8n" / "meridian.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        serialized = json.dumps(workflow)

        self.assertIn("body.message", serialized)


class MeridianRouterForwardingTests(unittest.IsolatedAsyncioTestCase):
    @patch("twm.routers.trip_matcher.engine")
    async def test_router_forwards_validated_phase_slice_and_message(
        self, engine
    ) -> None:
        engine.meridian.return_value = AgentExecution(
            response={
                "status": "NEEDS_CLARIFICATION",
                "message": "What budget should I use?",
                "state_delta": {
                    "matcher_state": {
                        "conversation_context": {
                            "last_meridian_message": "What budget should I use?",
                            "awaiting": "budget",
                        }
                    }
                },
                "options": [],
            },
            prompt_release=PromptRelease("meridian", "1.1.0", "prompt"),
        )
        request = MeridianRequest(
            trip_state={
                "trip_context": {"destination_scope": "mountains"},
                "advisor_state": {
                    "conversation_context": {
                        "last_advisor_message": "Here are the broad choices."
                    }
                },
                "matcher_state": {},
            },
            message="Please narrow those down.",
        )

        response = await meridian_route(request)

        engine.meridian.assert_called_once_with(
            request.trip_state.model_dump(), "Please narrow those down."
        )
        self.assertEqual(response.status, "NEEDS_CLARIFICATION")
        self.assertEqual(
            response.state_delta.matcher_state["conversation_context"]["awaiting"],
            "budget",
        )


class MeridianResponseContractTests(unittest.TestCase):
    @staticmethod
    def agent_meta() -> AgentMeta:
        return AgentMeta(agent="meridian", prompt_version="1.1.0")

    def test_success_omits_optional_constraint_adjustments(self) -> None:
        response = MeridianResponse(status="SUCCESS", agent_meta=self.agent_meta())

        payload = response.model_dump(exclude_none=True)
        self.assertNotIn("constraint_adjustment_suggestions", payload)
        self.assertNotIn("version", payload)
        self.assertNotIn("relaxation_suggestions", payload)

    def test_failure_allows_useful_constraint_adjustments(self) -> None:
        response = MeridianResponse(
            status="BUDGET_FAIL",
            constraint_adjustment_suggestions=["Increase the stay budget."],
            agent_meta=self.agent_meta(),
        )

        self.assertEqual(
            response.constraint_adjustment_suggestions,
            ["Increase the stay budget."],
        )

    def test_success_rejects_constraint_adjustments(self) -> None:
        with self.assertRaises(ValidationError):
            MeridianResponse(
                status="SUCCESS",
                constraint_adjustment_suggestions=["Change the dates."],
                agent_meta=self.agent_meta(),
            )

    def test_rejects_empty_constraint_adjustments(self) -> None:
        with self.assertRaises(ValidationError):
            MeridianResponse(
                status="SOFT_FAIL",
                constraint_adjustment_suggestions=[],
                agent_meta=self.agent_meta(),
            )

    def test_rejects_deprecated_response_fields(self) -> None:
        with self.assertRaises(ValidationError):
            MeridianResponse(
                status="SUCCESS",
                version="matcher_v2",
                agent_meta=self.agent_meta(),
            )

        with self.assertRaises(ValidationError):
            MeridianResponse(
                status="HARD_FAIL",
                relaxation_suggestions=["Change the budget."],
                agent_meta=self.agent_meta(),
            )

    def test_rejects_ui_owned_state_delta_fields(self) -> None:
        invalid_deltas = [
            {"stage": "recommended"},
            {"trip_context": {"selected_option": {"id": "one"}}},
            {"matcher_state": {"recommendations": [{"status": "SUCCESS"}]}},
        ]

        for state_delta in invalid_deltas:
            with self.subTest(state_delta=state_delta):
                with self.assertRaises(ValidationError):
                    MeridianResponse(
                        status="SUCCESS",
                        state_delta=state_delta,
                        agent_meta=self.agent_meta(),
                    )

    def test_rejects_unknown_business_status(self) -> None:
        with self.assertRaises(ValidationError):
            MeridianResponse(status="MISSING_INPUTS", agent_meta=self.agent_meta())

    def test_rejects_unknown_trip_type(self) -> None:
        with self.assertRaises(ValidationError):
            MeridianResponse(
                status="SUCCESS",
                trip_type="itinerary",
                agent_meta=self.agent_meta(),
            )


if __name__ == "__main__":
    unittest.main()
