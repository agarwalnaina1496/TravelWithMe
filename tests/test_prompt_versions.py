import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_prompt_version_changes import changed_prompts_requiring_release
from twm import prompts


class PromptVersionTests(unittest.TestCase):
    def test_current_release_files_are_valid(self):
        prompts.validate_prompt_release_files()
        self.assertEqual("1.0.0", prompts.load_prompt_version("scout"))
        self.assertEqual("1.0.0", prompts.load_prompt_version("meridian"))

    def test_load_prompt_release_keeps_content_and_version_together(self):
        release = prompts.load_prompt_release("scout")
        self.assertEqual("scout", release.agent)
        self.assertEqual("1.0.0", release.version)
        self.assertTrue(release.content.startswith("You are Scout"))

    def test_invalid_semver_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            versions_file = Path(temp_dir) / "versions.json"
            versions_file.write_text(
                json.dumps({"scout": "v1", "meridian": "1.0.0"}),
                encoding="utf-8",
            )
            with patch.object(prompts, "VERSIONS_FILE", versions_file):
                with self.assertRaisesRegex(ValueError, "Invalid semantic version"):
                    prompts.load_prompt_versions()

    def test_missing_agent_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            versions_file = Path(temp_dir) / "versions.json"
            versions_file.write_text(json.dumps({"scout": "1.0.0"}), encoding="utf-8")
            with patch.object(prompts, "VERSIONS_FILE", versions_file):
                with self.assertRaisesRegex(ValueError, "missing: meridian"):
                    prompts.load_prompt_versions()

    def test_changed_prompt_requires_version_bump(self):
        errors = changed_prompts_requiring_release(
            {"scout": "old", "meridian": "same"},
            {"scout": "new", "meridian": "same"},
            {"scout": "1.0.0", "meridian": "1.0.0"},
            {"scout": "1.0.0", "meridian": "1.0.0"},
            "## Scout 1.0.0",
        )
        self.assertEqual(["scout.md changed without a scout version bump"], errors)

    def test_changed_prompt_requires_current_changelog_heading(self):
        errors = changed_prompts_requiring_release(
            {"scout": "old", "meridian": "same"},
            {"scout": "new", "meridian": "same"},
            {"scout": "1.0.0", "meridian": "1.0.0"},
            {"scout": "1.1.0", "meridian": "1.0.0"},
            "## Scout 1.0.0",
        )
        self.assertEqual(
            ["scout.md changed but CHANGELOG.md is missing heading: ## Scout 1.1.0"],
            errors,
        )


if __name__ == "__main__":
    unittest.main()
