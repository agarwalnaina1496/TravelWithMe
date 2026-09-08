# TWM-223 architecture fitness function: LLM-provider SDKs are imported
# directly only inside the engine boundary. Everything else — routers,
# schemas, persistence, and the business services — reaches an agent
# through the AgentEngine / AgentAdapter protocol, never langchain /
# langgraph / groq directly.
#
# import-linter can't express "direct import only" (it traces transitively,
# and every caller of the engine transitively reaches langgraph), so this
# is the custom check AGENTS.md's "Architecture rules (enforced)" points at.

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "twm"

# The two packages that ARE the provider boundary.
ALLOWED_PREFIXES = ("twm/services/agent_engine/", "twm/services/langgraph/")

_SDK_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(langchain|langchain_groq|langgraph|groq)\b", re.MULTILINE
)


def main() -> int:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(ALLOWED_PREFIXES):
            continue
        for match in _SDK_IMPORT.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}: imports {match.group(1)} directly")

    if offenders:
        print("Provider-SDK boundary fitness function failed:")
        for line in offenders:
            print(f"  - {line}")
        print(
            "\nReach the agent through twm.services.agent_engine (AgentEngine), "
            "not the SDK directly."
        )
        return 1
    print(
        "Provider-SDK boundary OK (langchain / langgraph / groq imported only "
        "under services/agent_engine and services/langgraph)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
