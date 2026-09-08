# TWM-225 recurring architecture audit.
#
# The fitness functions (ruff / import-linter / the custom checks / the
# pytest fitness tests) catch a rule being *broken*. This audit catches a
# rule *decaying* — the tree drifting toward a break, or a fitness
# function's own assumptions going stale.
#
# It never fails the build. It prints a findings report; a scheduled CI job
# runs it quarterly and keeps the output as an artifact. If a threshold is
# crossed, a human opens a cleanup story (see AGENTS.md → "Architecture
# audit (quarterly)").
#
#   python scripts/architecture_audit.py

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "twm"

MODULE_HARD_CAP = 600
MODULE_WARN_AT = MODULE_HARD_CAP - 120  # 480 — "approaching the cap"
NOQA_C901_BUDGET = 5
IGNORE_IMPORTS_BUDGET = 3


def _py_files() -> list[Path]:
    return [p for p in sorted(PKG.rglob("*.py")) if "__pycache__" not in p.parts]


def check_module_sizes() -> list[str]:
    findings = []
    for p in _py_files():
        n = sum(1 for _ in p.open(encoding="utf-8"))
        if MODULE_WARN_AT <= n <= MODULE_HARD_CAP:
            findings.append(
                f"{p.relative_to(ROOT).as_posix()}: {n} lines — within {MODULE_HARD_CAP - n} of the cap"
            )
    return findings


def check_suppression_growth() -> list[str]:
    c901 = []
    other = []
    for p in _py_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "noqa: C901" in line or "noqa: PLR0912" in line:
                c901.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
            elif "# noqa" in line or "# type: ignore" in line:
                other.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
    findings = []
    if len(c901) > NOQA_C901_BUDGET:
        findings.append(
            f"C901/PLR0912 suppressions: {len(c901)} (budget {NOQA_C901_BUDGET}) — "
            + ", ".join(c901)
        )
    if other:
        findings.append(f"other # noqa / # type: ignore: {len(other)} — " + ", ".join(other))
    return findings


def check_import_linter_debt() -> list[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    findings = []
    for contract in cfg.get("tool", {}).get("importlinter", {}).get("contracts", []):
        ignored = contract.get("ignore_imports", [])
        if len(ignored) > IGNORE_IMPORTS_BUDGET:
            findings.append(
                f"contract {contract['name']!r}: {len(ignored)} ignored imports "
                f"(budget {IGNORE_IMPORTS_BUDGET}) — ratchet these away"
            )
        for entry in ignored:
            findings.append(f"ratcheted layering debt still present: {entry}")
    return findings


def check_elif_dispatch_chains() -> list[str]:
    findings = []
    for p in _py_files():
        # schema validators branch on a status/type discriminant by design;
        # the registry rule is about command / action dispatchers.
        if p.parts[p.parts.index("twm") + 1] == "schemas":
            continue
        if p.name == "handlers.py":  # this file IS the registry
            continue
        text = p.read_text(encoding="utf-8")
        for block in re.split(r"\n(?=\s*(?:async )?def )", text):
            branches = re.findall(
                r"^\s*(?:if|elif)\s+[\w.\[\]\"'()\s]*==\s*[\"'][\w-]+[\"']",
                block,
                re.MULTILINE,
            )
            if len(branches) >= 4:
                name = re.search(r"(?:async )?def (\w+)", block)
                findings.append(
                    f"{p.relative_to(ROOT).as_posix()}:{name.group(1) if name else '?'} — "
                    f"{len(branches)} `== \"literal\"` branches; a {{name: handler}} "
                    "registry?"
                )
    return findings


def check_unreferenced_routers() -> list[str]:
    # A route whose distinctive path segment appears in no test is a
    # candidate dead endpoint.
    test_text = " ".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "tests").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    findings = []
    for r in (PKG / "routers").glob("*.py"):
        for m in re.finditer(
            r'@router\.\w+\(\s*["\']([^"\']*)["\']', r.read_text(encoding="utf-8")
        ):
            segments = [s for s in m.group(1).split("/") if s and not s.startswith("{")]
            distinctive = segments[-1] if segments else r.stem
            if distinctive not in test_text:
                findings.append(
                    f"{r.relative_to(ROOT).as_posix()}: route {m.group(1)!r} "
                    f"(segment {distinctive!r}) not named in any test"
                )
    return findings


SECTIONS = [
    ("Modules approaching the size cap", check_module_sizes),
    ("Suppression growth (# noqa / # type: ignore)", check_suppression_growth),
    ("import-linter ratcheted debt", check_import_linter_debt),
    ("Possible if/elif dispatch chains", check_elif_dispatch_chains),
    ("Routes with no test coverage", check_unreferenced_routers),
]


def main() -> int:
    total = 0
    print("# Architecture audit — TravelWithMe\n")
    for title, fn in SECTIONS:
        findings = fn()
        total += len(findings)
        print(f"## {title}")
        if not findings:
            print("- clean\n")
            continue
        for line in findings:
            print(f"- {line}")
        print()

    print(f"---\n{total} finding(s). None fail the build; a human triages each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
