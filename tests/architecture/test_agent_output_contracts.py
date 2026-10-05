"""TWM-234: the agent output-contract fitness function.

Every model an LLM fills in must extend ``AgentContent`` (unknown keys are
dropped, not fatal) -- except the trust-critical ``state_delta`` branches,
which stay ``extra="forbid"`` on purpose: an agent reaching for a Backend-owned
key there is an injection signal worth rejecting loudly. A new content model
that quietly reverts to strict ``BaseModel`` would bring the "one harmless key
costs the whole response" failure mode back, so it fails here instead.

Behaviour of the tolerance itself is covered by the slip catalogue in
``tests/unit/agent_engine/test_output_tolerance.py``.
"""

import types
import typing

import pytest
from pydantic import BaseModel

from twm.schemas import AtlasAgentOutput, GuideAgentOutput, MeridianAgentOutput, ScoutAgentOutput
from twm.schemas.agent_contract import AgentContent

# Strict on purpose -- each guards ownership of persisted state.
STRICT_BY_DESIGN = {
    "ScoutStateDelta",
    "MeridianStateDelta",
    "GuideStateDelta",
    "GuidePlannerStateDelta",
    "GuideConversationContext",
    "TripContext",
}


def _models_in(annotation, seen: set[type]) -> None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation not in seen:
            seen.add(annotation)
            for field in annotation.model_fields.values():
                _models_in(field.annotation, seen)
        return
    for argument in typing.get_args(annotation):
        _models_in(argument, seen)


def reachable_models(root: type[BaseModel]) -> set[type]:
    seen: set[type] = set()
    _models_in(root, seen)
    return seen


@pytest.mark.parametrize(
    "output_model",
    [ScoutAgentOutput, MeridianAgentOutput, GuideAgentOutput, AtlasAgentOutput],
    ids=lambda model: model.__name__,
)
def test_every_llm_filled_model_tolerates_unknown_keys_unless_trust_critical(output_model):
    offenders = sorted(
        model.__name__
        for model in reachable_models(output_model)
        if not issubclass(model, AgentContent) and model.__name__ not in STRICT_BY_DESIGN
    )

    assert offenders == [], (
        f"{offenders} are filled in by an LLM but extend plain BaseModel; extend "
        "AgentContent (heal) or add to STRICT_BY_DESIGN with the reason it must reject."
    )


def test_the_strict_list_names_real_models():
    all_models = {
        model.__name__
        for output in (ScoutAgentOutput, MeridianAgentOutput, GuideAgentOutput, AtlasAgentOutput)
        for model in reachable_models(output)
    }

    assert STRICT_BY_DESIGN <= all_models | {"TripContext"}
