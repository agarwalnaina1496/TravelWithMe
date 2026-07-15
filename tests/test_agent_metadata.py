import unittest

from twm.prompts import PromptRelease
from twm.routers.trip_matcher import (
    _normalize_meridian_response,
    _normalize_scout_response,
)
from twm.services import AgentExecution


class AgentMetadataTests(unittest.TestCase):
    def test_scout_response_uses_backend_release_metadata(self) -> None:
        execution = AgentExecution(
            response={
                "message": "Hello",
                "state_delta": {},
                "agent_meta": {"agent": "meridian", "prompt_version": "999.0.0"},
            },
            prompt_release=PromptRelease("scout", "1.2.3", "prompt"),
        )

        response = _normalize_scout_response(execution)

        self.assertEqual(response.agent_meta.agent, "scout")
        self.assertEqual(response.agent_meta.prompt_version, "1.2.3")
        self.assertEqual(
            response.model_dump()["agent_meta"],
            {"agent": "scout", "prompt_version": "1.2.3"},
        )

    def test_meridian_response_overrides_model_provenance(self) -> None:
        execution = AgentExecution(
            response={
                "status": "SUCCESS",
                "state_delta": {},
                "version": "matcher_v2",
                "agent_meta": {"agent": "scout", "prompt_version": "999.0.0"},
            },
            prompt_release=PromptRelease("meridian", "2.4.0", "prompt"),
        )

        response = _normalize_meridian_response(execution)

        self.assertEqual(response.agent_meta.agent, "meridian")
        self.assertEqual(response.agent_meta.prompt_version, "2.4.0")
        self.assertNotIn("version", response.model_dump())


if __name__ == "__main__":
    unittest.main()
