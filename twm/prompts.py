from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

ALLOWED_PROMPTS = {"scout", "meridian"}


def load_prompt(agent: str) -> str:
    prompt_name = agent.lower()
    if prompt_name not in ALLOWED_PROMPTS:
        raise ValueError(f"Unknown prompt: {agent}")

    return (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8").strip()
