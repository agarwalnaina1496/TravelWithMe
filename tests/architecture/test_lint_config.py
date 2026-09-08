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


def test_ruff_selects_the_caps_rules(config):
    select = set(config["tool"]["ruff"]["lint"]["select"])
    assert {"C901", "PLR0912", "PLR0913", "PLR0915"} <= select


def test_complexity_cap_stays_at_or_below_twelve(config):
    assert config["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] <= 12


def test_arity_cap_stays_at_or_below_six(config):
    assert config["tool"]["ruff"]["lint"]["pylint"]["max-args"] <= 6


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


def test_the_only_ratcheted_layer_exception_is_the_known_postgres_debt(config):
    contracts = config["tool"]["importlinter"]["contracts"]
    layers = next(c for c in contracts if c["type"] == "layers")
    assert layers.get("ignore_imports", []) == [
        "twm.persistence.postgres -> twm.services.trip_commands.state"
    ]


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
