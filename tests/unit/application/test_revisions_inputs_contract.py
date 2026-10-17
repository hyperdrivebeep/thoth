"""Revision inputs retain schemas, graph bounds and the external restore proposal imports."""

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import restore_proposals
from thoth.application.commands import revisions_full as owner

SCHEMAS = cast(
    dict[str, object],
    json.loads((Path(__file__).parents[2] / "fixtures/revisions_inputs_schemas.json").read_bytes()),
)


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_original_schema_and_legacy_identity(name: str) -> None:
    module = importlib.import_module(owner.RevisionListInput.__module__)
    model = getattr(module, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert getattr(owner, name) is model
    assert model.model_json_schema() == SCHEMAS[name]


@pytest.mark.parametrize(
    "name", ("ChangeSetCommitInput", "ChangeSetValidateInput", "RestoreProposeInput")
)
def test_restore_proposals_imports_the_same_class(name: str) -> None:
    assert getattr(restore_proposals, name) is getattr(owner, name)


def test_list_graph_defaults_frozen_and_extra() -> None:
    item = owner.RevisionListInput(project_id="p")
    assert item.model_dump() == {
        "project_id": "p",
        **dict.fromkeys(("aggregate_id", "aggregate_type", "commit_state", "freshness")),
    }
    graph = owner.GraphReadInput(project_id="p")
    assert graph.depth == 20 and graph.relation_types == ()
    for depth in (1, 1000):
        assert owner.GraphReadInput(project_id="p", depth=depth).depth == depth
    for depth in (0, 1001):
        with pytest.raises(ValidationError):
            owner.GraphReadInput(project_id="p", depth=depth)
    with pytest.raises(ValidationError) as extra:
        owner.ProjectInput.model_validate({"project_id": "p", "extra": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        item.project_id = "q"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


def test_proposal_defaults_enum_and_nested_json() -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "aggregate_id": "a",
        "aggregate_type": "THREAD",
        "parent_revision_digests": ["z", "a"],
        "candidate_content": {"nested": [None, False, 1]},
        "reason": "reason",
        "evidence_refs": [],
        "actor_or_agent_ref": "human",
    }
    item = owner.ProposeInput.model_validate(data)
    assert item.candidate_schema_version == "1.0.0" and item.expected_head_digest is None
    assert item.parent_revision_digests == ("z", "a")
    assert item.model_dump(mode="json")["candidate_content"] == data["candidate_content"]
    with pytest.raises(ValidationError):
        owner.ProposeInput.model_validate({**data, "aggregate_type": "INVALID"})
    for digest in ("", "z" * 63, "z" * 65):
        with pytest.raises(ValidationError):
            owner.ProposeInput.model_validate({**data, "expected_head_digest": digest})


def test_merge_parent_minimum_preserves_order_and_allows_existing_duplicates() -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "aggregate_id": "a",
        "merge_policy_ref": "policy",
        "reason": "merge",
        "evidence_refs": [],
    }
    with pytest.raises(ValidationError):
        owner.MergeProposeInput.model_validate({**data, "parent_revision_digests": ["one"]})
    for refs in (["z", "a"], ["same", "same"]):
        item = owner.MergeProposeInput.model_validate({**data, "parent_revision_digests": refs})
        assert item.parent_revision_digests == tuple(refs) and item.candidate_content is None


def test_restore_and_commit_digest_lengths_and_revision_bounds() -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "aggregate_id": "a",
        "current_head_digest": "x" * 64,
        "target_revision_digest": "z" * 64,
        "reason": "restore",
        "evidence_refs": ["b", "a"],
        "actor_or_agent_ref": "human",
    }
    assert restore_proposals.RestoreProposeInput.model_validate(data).evidence_refs == ("b", "a")
    for key in ("current_head_digest", "target_revision_digest"):
        with pytest.raises(ValidationError):
            restore_proposals.RestoreProposeInput.model_validate({**data, key: "short"})
    assert restore_proposals.ChangeSetValidateInput(
        project_id="p", record_id="r", expected_change_set_revision=1
    )
    with pytest.raises(ValidationError):
        restore_proposals.ChangeSetValidateInput(
            project_id="p", record_id="r", expected_change_set_revision=0
        )
    commit = {
        "project_id": "p",
        "record_id": "r",
        "validation_bundle_digest": "v" * 64,
        "expected_head_set_digest": "h" * 64,
    }
    assert restore_proposals.ChangeSetCommitInput.model_validate(commit).model_dump() == commit


@pytest.mark.parametrize("decision", ("APPROVE", "REJECT"))
def test_baseline_decision_vocabulary_and_optional_reason(decision: str) -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "baseline_candidate_id": "b",
        "decision": decision,
        "actor_ref": "human",
        "role_assignment_ref": "r",
        "approved_digest": "z" * 64,
    }
    assert owner.BaselineDecideInput.model_validate(data).reason is None
    assert owner.BaselineDecideInput.model_validate({**data, "reason": ""}).reason == ""
    with pytest.raises(ValidationError):
        owner.BaselineDecideInput.model_validate({**data, "decision": decision.lower()})
