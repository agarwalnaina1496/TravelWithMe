import json
import re
from dataclasses import dataclass
from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

ALLOWED_PROMPTS = {"scout", "meridian"}
VERSIONS_FILE = PROMPTS_DIR / "versions.json"
CHANGELOG_FILE = PROMPTS_DIR / "CHANGELOG.md"
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True)
class PromptRelease:
    agent: str
    version: str
    content: str


def _normalize_agent(agent: str) -> str:
    prompt_name = agent.lower()
    if prompt_name not in ALLOWED_PROMPTS:
        raise ValueError(f"Unknown prompt: {agent}")
    return prompt_name


def load_prompt_versions() -> dict[str, str]:
    try:
        raw_versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Prompt versions file is missing: {VERSIONS_FILE}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Prompt versions file is not valid JSON: {VERSIONS_FILE}") from exc

    if not isinstance(raw_versions, dict):
        raise ValueError("Prompt versions must be a JSON object")

    expected = ALLOWED_PROMPTS
    actual = set(raw_versions)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown: {', '.join(unknown)}")
        raise ValueError(f"Prompt versions must define exactly {sorted(expected)} ({'; '.join(details)})")

    versions: dict[str, str] = {}
    for agent, version in raw_versions.items():
        if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
            raise ValueError(f"Invalid semantic version for {agent}: {version!r}")
        versions[agent] = version
    return versions


def load_prompt_version(agent: str) -> str:
    return load_prompt_versions()[_normalize_agent(agent)]


def load_prompt(agent: str) -> str:
    prompt_name = _normalize_agent(agent)
    return (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8").strip()


def load_prompt_release(agent: str) -> PromptRelease:
    prompt_name = _normalize_agent(agent)
    return PromptRelease(
        agent=prompt_name,
        version=load_prompt_version(prompt_name),
        content=load_prompt(prompt_name),
    )


def validate_prompt_release_files() -> None:
    versions = load_prompt_versions()
    try:
        changelog = CHANGELOG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Prompt changelog is missing: {CHANGELOG_FILE}") from exc

    for agent, version in versions.items():
        heading = f"## {agent.title()} {version}"
        if heading not in changelog:
            raise ValueError(f"Prompt changelog is missing heading: {heading}")
