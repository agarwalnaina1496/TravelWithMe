"""TWM-223 fitness-function helper: single-source composition.

Generalises TWM-217's ``test_composer_owns_every_field``. Every non-structural
field of a *composed response model* must be produced by a
``<composer>._compose_<field>`` method that the composer's build method
actually calls — so a future field cannot be wired straight from a router or
a repository read, bypassing the single composer.
"""

import inspect
from typing import Iterable


def assert_all_fields_composed(
    response_model: type,
    composer_cls: type,
    *,
    build_method: str = "build",
    structural: Iterable[str] = (),
) -> None:
    """Fail if any non-structural ``response_model`` field is not owned by a
    ``composer_cls._compose_<field>`` method that ``composer_cls.<build_method>``
    calls.

    ``structural`` names the fields that are pass-through identity / stored
    bags (e.g. ``id``, ``version``, ``ui_state``) — everything else is
    expected to be composed.
    """

    structural = set(structural)
    build_source = inspect.getsource(getattr(composer_cls, build_method))
    fields = getattr(response_model, "model_fields", None)
    assert fields, f"{response_model.__name__} is not a Pydantic model"

    missing_structural = structural - set(fields)
    assert not missing_structural, (
        f"structural exemptions name fields {response_model.__name__} does not "
        f"have: {sorted(missing_structural)}"
    )

    offenders = []
    for name in fields:
        if name in structural:
            continue
        method = f"_compose_{name}"
        if not hasattr(composer_cls, method):
            offenders.append(f"{name}: no {composer_cls.__name__}.{method}")
        elif f"self.{method}(" not in build_source:
            offenders.append(
                f"{name}: {method} exists but {build_method}() never calls it"
            )

    assert not offenders, (
        f"{response_model.__name__} fields not owned by {composer_cls.__name__} "
        f"(add a {method_hint(composer_cls)} method, or add a genuinely "
        f"structural field to the `structural` set): " + "; ".join(offenders)
    )


def method_hint(composer_cls: type) -> str:
    return f"{composer_cls.__name__}._compose_<field>"
