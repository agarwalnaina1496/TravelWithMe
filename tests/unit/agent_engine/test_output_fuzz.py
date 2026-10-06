"""TWM-234: a healer or validator must never crash on a hostile shape.

The slip catalogue (``test_tolerance_*.py``) proves the slips we thought
of heal. This is the other half: seeded, random damage to every recorded
completion -- wrong types, nulls, empties, huge numbers, deleted keys -- must
only ever produce a clean ``ValidationError`` (a retry) or a valid response.
Any other exception would be a 500 for the traveler.
"""

import copy
import glob
import json
import random

import pytest
from pydantic import ValidationError

from twm.schemas import AtlasAgentOutput, GuideAgentOutput, MeridianAgentOutput, ScoutAgentOutput
from twm.schemas.agent_contract import HEALED_KEY
from twm.services.agent_engine.json_decoding import decode_agent_json, relax_json

MODELS = {
    "meridian": MeridianAgentOutput,
    "guide": GuideAgentOutput,
    "atlas": AtlasAgentOutput,
    "scout": ScoutAgentOutput,
}
JUNK = [
    None, [], {}, "", " ", 0, -1, 3, 1.5, 1e308, True, False, "x", "1,500", "TRUE",
    [None], [[]], {"a": 1}, ["a", "A"], "​", 10**30, "-", "None", ["a", ["b"]], [{"a": 1}],
]
MUTATIONS_PER_FIXTURE = 40


def _paths(node, prefix=()):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += [prefix + (key,)] + _paths(value, prefix + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += [prefix + (index,)] + _paths(value, prefix + (index,))
    return found


def _damage(data, rng):
    for _ in range(rng.randint(1, 3)):
        paths = _paths(data)
        if not paths:
            return
        path = rng.choice(paths)
        parent = data
        for step in path[:-1]:
            parent = parent[step]
        if rng.random() < 0.2:
            if isinstance(parent, dict):
                parent.pop(path[-1], None)
            else:
                del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(rng.choice(JUNK))


def _fixtures():
    for path in sorted(glob.glob("tests/resources/harness_fixtures/*.json")):
        agent = path.replace("\\", "/").split("/")[-1].split("__")[0]
        with open(path, encoding="utf8") as handle:
            yield agent, json.loads(json.loads(handle.read())["raw_output"])


def test_damaged_outputs_only_ever_fail_with_a_validation_error():
    rng = random.Random(1234)
    runs = 0
    for agent, base in _fixtures():
        for _ in range(MUTATIONS_PER_FIXTURE):
            data = copy.deepcopy(base)
            _damage(data, rng)
            runs += 1
            try:
                MODELS[agent].model_validate(data, context={HEALED_KEY: []})
            except ValidationError:
                pass
    assert runs > 1000


def test_decoding_arbitrary_text_only_ever_fails_with_a_json_error():
    rng = random.Random(7)
    alphabet = list("{}[],:\"\\/*'\n \tTrueFalsNon0123456789.-eE")
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        try:
            decode_agent_json(text)
        except json.JSONDecodeError:
            pass
        relax_json(text)


@pytest.mark.parametrize("seed", range(5))
def test_relaxing_valid_json_never_changes_what_it_says(seed):
    rng = random.Random(seed)
    tricky = ["None", "True", "False", "// x", "/* x */", "a,]", "trailing,}", '"quoted"', "line\nbreak", "\\"]

    def value(depth=0):
        kind = rng.randint(0, 5 if depth < 3 else 3)
        if kind == 0:
            return rng.choice(tricky)
        if kind == 1:
            return rng.randint(-5, 5)
        if kind == 2:
            return rng.choice([None, True, False])
        if kind == 3:
            return "".join(rng.choice("ab ,]}/*") for _ in range(rng.randint(0, 6)))
        if kind == 4:
            return [value(depth + 1) for _ in range(rng.randint(0, 3))]
        return {rng.choice(tricky): value(depth + 1) for _ in range(rng.randint(0, 3))}

    for _ in range(200):
        original = value()
        assert json.loads(relax_json(json.dumps(original))) == original
