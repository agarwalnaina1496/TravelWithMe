"""Require prompt behavior changes to carry a version bump and changelog entry."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "twm" / "prompts"
AGENTS = ("scout", "meridian")


def git_show(base_ref: str, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{relative_path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Unable to read {relative_path} from {base_ref}: {result.stderr.strip()}"
        )
    return result.stdout


def changed_prompts_requiring_release(
    base_prompts: dict[str, str],
    current_prompts: dict[str, str],
    base_versions: dict[str, str],
    current_versions: dict[str, str],
    changelog: str,
) -> list[str]:
    errors: list[str] = []
    for agent in AGENTS:
        if base_prompts[agent] == current_prompts[agent]:
            continue
        if base_versions.get(agent) == current_versions.get(agent):
            errors.append(f"{agent}.md changed without a {agent} version bump")
            continue
        heading = f"## {agent.title()} {current_versions.get(agent, '')}"
        if heading not in changelog:
            errors.append(f"{agent}.md changed but CHANGELOG.md is missing heading: {heading}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "base_ref",
        nargs="?",
        default="origin/main",
        help="Git ref to compare against (default: origin/main)",
    )
    args = parser.parse_args()

    base_prompts = {
        agent: git_show(args.base_ref, f"twm/prompts/{agent}.md") for agent in AGENTS
    }
    current_prompts = {
        agent: (PROMPTS_DIR / f"{agent}.md").read_text(encoding="utf-8")
        for agent in AGENTS
    }
    try:
        base_versions = json.loads(git_show(args.base_ref, "twm/prompts/versions.json"))
    except RuntimeError:
        # Bootstrap: the base predates versioning, so the current files establish
        # the first release instead of requiring a bump from an unknown version.
        base_versions = {
            agent: current_versions
            for agent, current_versions in json.loads(
                (PROMPTS_DIR / "versions.json").read_text(encoding="utf-8")
            ).items()
        }
        base_prompts = current_prompts

    current_versions = json.loads(
        (PROMPTS_DIR / "versions.json").read_text(encoding="utf-8")
    )
    changelog = (PROMPTS_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    errors = changed_prompts_requiring_release(
        base_prompts,
        current_prompts,
        base_versions,
        current_versions,
        changelog,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Prompt version policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
