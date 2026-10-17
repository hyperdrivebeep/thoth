"""Memory input schemas and validation are fixed independently of the extraction."""

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import memory_full as owner

SCHEMAS = cast(
    dict[str, object],
    json.loads((Path(__file__).parents[2] / "fixtures/memory_inputs_schemas.json").read_bytes()),
)


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_original_schema_and_legacy_identity(name: str) -> None:
    module = importlib.import_module(owner.MemoryListInput.__module__)
    model = getattr(module, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert getattr(owner, name) is model
    assert model.model_json_schema() == SCHEMAS[name]


def test_defaults_extra_and_frozen() -> None:
    value = owner.MemoryListInput(project_id="p")
    assert value.model_dump() == {
        "project_id": "p",
        **dict.fromkeys(
            (
                "recall_class",
                "payload_mode",
                "pipeline_status",
                "lifecycle_status",
                "recall_eligibility",
            )
        ),
    }
    assert owner.PolicyListInput(project_id="p").enabled_only is True
    assert owner.ContextReadInput(project_id="p").character_budget == 20000
    with pytest.raises(ValidationError) as extra:
        owner.ProjectInput.model_validate({"project_id": "p", "extra": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        value.project_id = "q"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


def test_budgets_revisions_and_nullable_digest_boundaries() -> None:
    for budget in (0, 2000000):
        assert (
            owner.ContextReadInput(project_id="p", character_budget=budget).character_budget
            == budget
        )
    for budget in (-1, 2000001):
        with pytest.raises(ValidationError):
            owner.ContextReadInput(project_id="p", character_budget=budget)
    assert owner.PolicyReadInput(project_id="p", profile_ref="x").version is None
    with pytest.raises(ValidationError):
        owner.PolicyReadInput(project_id="p", profile_ref="x", version=0)
    assert owner.MemoryReadInput(project_id="p", memory_entry_id="m").revision_digest is None
    for digest in ("", "z" * 63, "z" * 65):
        with pytest.raises(ValidationError):
            owner.MemoryReadInput(project_id="p", memory_entry_id="m", revision_digest=digest)
    assert owner.MemoryReadInput(project_id="p", memory_entry_id="m", revision_digest="z" * 64)


def test_candidate_vocabulary_reference_order_and_empty_fields() -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "producer_role": "agent",
        "payload_mode": "MEMORY_ASSERTION",
        "recall_class": "LESSON",
        "scope": {},
        "evidence_refs": ["b", "a", "b"],
    }
    value = owner.CandidateCreateInput.model_validate(payload)
    assert value.evidence_refs == ("b", "a", "b") and value.parent_refs == value.risk_tags == ()
    assert value.domain_revision_ref is None and value.assertion_candidate is None
    assert owner.CandidateCreateInput.model_validate(
        {**payload, "payload_mode": "DOMAIN_REFERENCE"}
    )
    with pytest.raises(ValidationError):
        owner.CandidateCreateInput.model_validate({**payload, "payload_mode": "OTHER"})


def test_context_cutoff_and_retention_require_aware_time() -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": "t",
        "query": "query",
        "target_use": "research",
        "scope": {},
        "token_budget": 0,
        "selection_policy_version": "v1",
    }
    assert owner.ContextBuildInput.model_validate(payload).cutoff is None
    for time in ("2026-10-12T00:00:00Z", "2026-10-12T09:00:00+09:00"):
        assert (
            owner.ContextBuildInput.model_validate({**payload, "cutoff": time}).cutoff is not None
        )
        assert (
            owner.RetentionEvaluateInput.model_validate(
                {"project_id": "p", "policy_version": "v1", "evaluation_time": time}
            ).scope
            is None
        )
    with pytest.raises(ValidationError):
        owner.ContextBuildInput.model_validate({**payload, "cutoff": "2026-10-12T00:00:00"})
    with pytest.raises(ValidationError):
        owner.RetentionEvaluateInput.model_validate(
            {"project_id": "p", "policy_version": "v1", "evaluation_time": "2026-10-12T00:00:00"}
        )
