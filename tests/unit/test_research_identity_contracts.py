"""Public contract descriptions and runtime semantics, collected before definition movement."""

import inspect
import json
from dataclasses import MISSING, FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from typing import get_type_hints

import pytest

from thoth.application.services import research_identity_service as identity
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord
from thoth.domain.revision import StagedRevision

NAMES = (
    "ResearchContext",
    "HypothesisOwnershipBatch",
    "ActionOwnershipBatch",
    "ResearchOwnershipBatch",
)


def descriptions() -> dict[str, object]:
    classes: dict[str, type[object]] = {
        "ResearchContext": identity.ResearchContext,
        "HypothesisOwnershipBatch": identity.HypothesisOwnershipBatch,
        # Resolve the internal batch through the public composite's real annotation.
        "ActionOwnershipBatch": get_type_hints(identity.ResearchOwnershipBatch)["actions"],
        "ResearchOwnershipBatch": identity.ResearchOwnershipBatch,
    }
    result: dict[str, object] = {}
    for name, cls in classes.items():
        assert is_dataclass(cls)
        result[name] = {
            "signature": str(inspect.signature(cls)),
            "fields": [
                {
                    "name": field.name,
                    "type": str(field.type),
                    "required": field.default is MISSING and field.default_factory is MISSING,
                    "default": None if field.default is MISSING else repr(field.default),
                    "factory": None
                    if field.default_factory is MISSING
                    else str(field.default_factory),
                }
                for field in fields(cls)
            ],
        }
    return result


@pytest.mark.parametrize("name", NAMES)
def test_original_dataclass_contract(name: str) -> None:
    expected = json.loads(Path(__file__).with_name("research_identity_contracts.json").read_text())
    assert descriptions()[name] == expected[name]


def test_context_default_factory_is_independent_and_heads_are_not_copied() -> None:
    heads = {"head": "digest"}
    first = identity.ResearchContext("p", "o", heads)
    second = identity.ResearchContext("p", "o", heads)
    assert first.heads is heads and second.heads is heads
    assert isinstance(first.currentness, dict) and isinstance(second.currentness, dict)
    assert first.currentness == second.currentness == {}
    assert first.currentness is not second.currentness


def batches(
    hyp_count: int, action_count: int
) -> tuple[identity.HypothesisOwnershipBatch, object, identity.ResearchOwnershipBatch]:
    hypotheses = identity.HypothesisOwnershipBatch(
        (),
        HypothesisPortfolioRecord.model_construct(),
        tuple(StagedRevision.model_construct() for _ in range(hyp_count)),
    )
    action_type = get_type_hints(identity.ResearchOwnershipBatch)["actions"]
    actions = action_type(
        (),
        ActionPortfolioRecord.model_construct(),
        ActionPlanRecord.model_construct(),
        tuple(StagedRevision.model_construct() for _ in range(action_count)),
    )
    return hypotheses, actions, identity.ResearchOwnershipBatch(hypotheses, actions)


def test_all_four_contracts_are_frozen() -> None:
    h, a, batch = batches(1, 1)
    for instance in (identity.ResearchContext("p", "o", {}), h, a, batch):
        assert is_dataclass(instance)
        with pytest.raises(FrozenInstanceError):
            setattr(instance, fields(instance)[0].name, "changed")


@pytest.mark.parametrize(("hyp_count", "action_count"), [(3, 2), (1, 1), (0, 0)])
def test_additional_staged_order_identity_and_input_tuples(
    hyp_count: int, action_count: int
) -> None:
    hypotheses, _actions, batch = batches(hyp_count, action_count)
    hyp_before, action_before = hypotheses.staged, batch.actions.staged
    expected = (*hyp_before[1:], *action_before[1:])
    actual = batch.additional_staged
    assert len(actual) == len(expected)
    assert all(item is prior for item, prior in zip(actual, expected, strict=True))
    assert hypotheses.staged is hyp_before and batch.actions.staged is action_before
