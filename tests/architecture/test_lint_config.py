"""TWM-223 PR B: guard the ruff / import-linter config.

The layer graph and the caps are enforced by ``ruff`` and ``lint-imports``
in CI. This test — which runs in the normal pytest step — guards the config
itself so a rule cannot be quietly downgraded or deleted: the check for the
check. It reads only ``pyproject.toml``; it does not need ruff or
import-linter installed.
"""

import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture(scope="module")
def config() -> dict:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))


def test_ruff_selects_the_god_function_rules(config):
    select = set(config["tool"]["ruff"]["lint"]["select"])
    assert {"C901", "PLR0912", "PLR0915"} <= select


def test_complexity_cap_stays_at_or_below_twelve(config):
    assert config["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] <= 12


def test_branch_and_statement_caps_hold(config):
    pylint = config["tool"]["ruff"]["lint"]["pylint"]
    assert pylint["max-branches"] <= 15
    assert pylint["max-statements"] <= 60


def test_the_noqa_c901_list_stays_short(config):
    # C901 / PLR0912 suppressions are allowed only with an inline reason and
    # must stay countable. If this climbs, tighten the code, not the budget.
    root = Path(__file__).resolve().parents[2] / "twm"
    hits = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "noqa: C901" in line or "noqa: PLR0912" in line
    ]
    assert len(hits) <= 5, f"too many complexity suppressions: {hits}"


def test_layer_graph_contract_is_the_four_layer_stack(config):
    contracts = config["tool"]["importlinter"]["contracts"]
    layers = next(c for c in contracts if c["type"] == "layers")
    assert layers["layers"] == [
        "twm.routers",
        "twm.services",
        "twm.persistence",
        "twm.schemas",
    ]


def test_router_persistence_internals_are_forbidden(config):
    contracts = config["tool"]["importlinter"]["contracts"]
    forbidden = [
        c
        for c in contracts
        if c["type"] == "forbidden" and c["source_modules"] == ["twm.routers"]
    ]
    assert forbidden, "the routers -> persistence-internals forbidden contract is gone"
    assert "twm.persistence.postgres" in forbidden[0]["forbidden_modules"]


def test_the_layers_contract_has_no_ratcheted_exceptions(config):
    # The one pre-existing exception (persistence.postgres -> trip_commands
    # .state) was removed in TWM-225 by moving TOUCHABLE_BRANCHES /
    # populated_touchable_branches to twm.shared. Keep this at zero.
    contracts = config["tool"]["importlinter"]["contracts"]
    layers = next(c for c in contracts if c["type"] == "layers")
    assert layers.get("ignore_imports", []) == []


def test_provider_sdk_boundary_check_covers_every_sdk(config):
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "check_provider_sdk_boundary.py"
    ).read_text(encoding="utf-8")
    for sdk in ("langchain", "langchain_groq", "langgraph", "groq"):
        assert sdk in script
    assert "twm/services/agent_engine/" in script
    assert "twm/services/langgraph/" in script
