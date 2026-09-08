# TWM-223 architecture fitness function: no module in twm/ over the hard
# line cap. ruff has no per-file line rule, so this is the custom check the
# "Architecture rules (enforced)" section of AGENTS.md points at.
#
# A file legitimately over the cap needs an explicit entry in EXEMPT with a
# one-line reason. Keep that list short and shrinking.

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "twm"
HARD_CAP = 600

# path relative to repo root -> reason. Ratchet these down; never add
# without a reason, never raise the cap.
EXEMPT: dict[str, str] = {
    "twm/persistence/postgres.py": "one cohesive asyncpg repository implementation",
    "twm/schemas/trusted_action.py": "one capability's full request/response contract",
    "twm/schemas/flight_search.py": "one capability's full request/response contract",
}


def main() -> int:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        lines = sum(1 for _ in path.open(encoding="utf-8"))
        if lines <= HARD_CAP:
            if rel in EXEMPT and lines <= HARD_CAP - 100:
                offenders.append(
                    f"{rel}: {lines} lines — now well under the cap, remove its EXEMPT entry"
                )
            continue
        if rel in EXEMPT:
            continue
        offenders.append(
            f"{rel}: {lines} lines exceeds the {HARD_CAP}-line module cap "
            "(split it, or add an EXEMPT entry with a reason)"
        )

    if offenders:
        print("Module size fitness function failed:")
        for line in offenders:
            print(f"  - {line}")
        return 1
    print(f"Module size OK (cap {HARD_CAP}, {len(EXEMPT)} documented exemptions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
