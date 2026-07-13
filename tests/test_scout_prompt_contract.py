import unittest

from twm.prompts import load_prompt, load_prompt_version


class ScoutPromptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = load_prompt("scout")

    def test_release_version_is_bumped(self) -> None:
        self.assertEqual(load_prompt_version("scout"), "1.1.0")

    def test_context_is_flat_and_does_not_store_whole_query(self) -> None:
        self.assertIn(
            "Do not create phase buckets such as `advisor`, `matcher`, or `planner`",
            self.prompt,
        )
        self.assertIn(
            "Do not store the full user message, question, or request as a context value",
            self.prompt,
        )
        self.assertNotIn(
            "Put all matcher-related signals under `trip_context.matcher`",
            self.prompt,
        )

    def test_scout_does_not_generate_phase_owned_memory(self) -> None:
        self.assertIn(
            "Do not write `advisor_state`, `matcher_state`, or `planner_state`",
            self.prompt,
        )
        self.assertNotIn('"assistant_message": "same text as message"', self.prompt)


if __name__ == "__main__":
    unittest.main()
