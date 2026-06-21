from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

PROMPT_FILES = {
    "scout": "SCOUT_SYSTEM_PROMPT.md",
    "meridian": "MERIDIAN_SYSTEM_PROMPT.md",
}


def load_prompt(name: str) -> str:
    try:
        filename = PROMPT_FILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown prompt: {name}") from exc

    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
