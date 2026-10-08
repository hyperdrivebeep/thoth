"""Object input schema, actor defaults, revision bounds and authority vocabulary stay fixed."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import objects_full

FIXTURE = Path(__file__).parents[2] / "fixtures/object_input_schemas.json"
SCHEMAS = cast(dict[str, object], json.loads(FIXTURE.read_text(encoding="utf-8")))


def input_model(name: str) -> type[BaseModel]:
    owner = importlib.import_module(objects_full.ObjectListInput.__module__)
    model = getattr(owner, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    return model


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_input_schema_matches_original(name: str) -> None:
    assert input_model(name).model_json_schema() == SCHEMAS[name]


def test_object_list_defaults_bounds_extra_and_frozen_contract() -> None:
    value = objects_full.ObjectListInput(project_id="p")
    assert value.model_dump(mode="json") == {
        "project_id": "p",
        "thread_id": None,
        "lifecycle": None,
        "active_work_mode": None,
        "profile_ref": None,
        "attention": None,
        "blocker": None,
    }
    assert objects_full.ObjectListInput(project_id="x" * 160).project_id == "x" * 160
    for invalid in ("", "x" * 161):
        with pytest.raises(ValidationError):
            objects_full.ObjectListInput(project_id=invalid)
    with pytest.raises(ValidationError) as extra:
        objects_full.ObjectListInput.model_validate({"project_id": "p", "extra": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        value.project_id = "q"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


def test_materialize_preserves_actor_optional_fields_order_and_revision_minimum() -> None:
    model = input_model("MaterializeInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": "thread:1",
        "purpose_statement": "Purpose",
        "focus_refs": ["z", "a"],
        "trigger_evidence_refs": [],
    }
    result = model.model_validate(payload).model_dump(mode="json")
    assert result["actor_ref"] == "agent:object-materializer"
    assert result["focus_refs"] == ["z", "a"] and result["profile_refs"] == []
    assert result["candidate_id"] is None and result["problem_frame"] is None
    assert result["expected_project_revision"] is None
    assert model.model_validate({**payload, "expected_project_revision": 0})
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "expected_project_revision": -1})


def test_profile_defaults_and_version_minimum() -> None:
    assert (
        input_model("ProfileListInput")
        .model_validate({"project_id": "p"})
        .model_dump()["enabled_only"]
        is True
    )
    model = input_model("ProfileReadInput")
    payload = {"project_id": "p", "profile_ref": "profile:1"}
    assert model.model_validate(payload).model_dump()["version"] is None
    assert model.model_validate({**payload, "version": 1})
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "version": 0})


@pytest.mark.parametrize("authority", ["UNCLASSIFIED", "INFORMAL", "OFFICIAL", "APPROVED"])
def test_relation_authority_pattern_actor_default_and_revision_length(authority: str) -> None:
    model = input_model("RelationAddInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "object_id": "object:1",
        "expected_revision_digest": "z" * 64,
        "relation_type": "DEPENDS_ON",
        "target_ref": "target:1",
        "semantic_role": "Role",
        "evidence_refs": [],
        "authority_state": authority,
    }
    result = model.model_validate(payload).model_dump(mode="json")
    assert (
        result["authority_state"] == authority and result["actor_ref"] == "agent:relation-proposer"
    )
    assert result["valid_time"] is None and result["revision_digest"] is None
    with pytest.raises(ValidationError) as pattern:
        model.model_validate({**payload, "authority_state": authority.lower()})
    assert pattern.value.errors()[0]["type"] == "string_pattern_mismatch"
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "expected_revision_digest": "z" * 63})


def test_followup_scope_default_and_merge_minimum() -> None:
    result = (
        input_model("FollowupCreateInput")
        .model_validate(
            {
                "project_id": "p",
                "object_id": "object:1",
                "expected_revision_digest": "a" * 64,
                "purpose_statement": "Follow up",
                "trigger_refs": [],
            }
        )
        .model_dump(mode="json")
    )
    assert result["inherit_scope"] is True
    model = input_model("MergeProposeInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "object_ids": ["z", "a"],
        "field_mapping": {},
        "evidence_refs": [],
        "rationale": "Reason",
        "expected_revision_digests": ["z" * 64, "a" * 64],
    }
    assert model.model_validate(payload).model_dump()["object_ids"] == ("z", "a")
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "object_ids": ["z"]})
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "expected_revision_digests": ["z" * 64]})
