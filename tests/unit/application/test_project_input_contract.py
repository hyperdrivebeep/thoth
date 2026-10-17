"""Project schemas and the restore handler's imported input keep their original contracts."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import project_restore, projects

SCHEMAS = cast(
    dict[str, object],
    json.loads((Path(__file__).parents[2] / "fixtures/project_input_schemas.json").read_bytes()),
)


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_schema_and_existing_import_identity(name: str) -> None:
    owner = importlib.import_module(projects.ProjectCreateInput.__module__)
    model = getattr(owner, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert getattr(projects, name) is model
    assert model.model_json_schema() == SCHEMAS[name]


def test_restore_uses_the_same_revision_bound_input() -> None:
    assert project_restore.ProjectExpectedRevisionInput is projects.ProjectExpectedRevisionInput
    request = project_restore.ProjectExpectedRevisionInput(project_id="p", expected_revision=0)
    assert request.model_dump(mode="json") == {"project_id": "p", "expected_revision": 0}
    with pytest.raises(ValidationError):
        project_restore.ProjectExpectedRevisionInput(project_id="p", expected_revision=-1)


def test_create_list_defaults_extra_fields_and_frozen_input() -> None:
    value = projects.ProjectCreateInput.model_validate(
        {"project_id": "p", "name": "Project", "cutoff_at": "2026-10-12T00:00:00Z"}
    )
    assert value.model_dump(mode="json") == {
        "project_id": "p",
        "name": "Project",
        "description": "",
        "cutoff_at": "2026-10-12T00:00:00Z",
        "overlay": "default",
        "policy_binding_ref": "policy:default",
    }
    assert projects.ProjectListInput().model_dump(mode="json") == {
        "project_id": "system:projects",
        "include_archived": False,
    }
    with pytest.raises(ValidationError) as extra:
        projects.ProjectListInput.model_validate({"unexpected": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        value.name = "changed"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


@pytest.mark.parametrize(
    "kind", ("ProjectCreateInput", "ProjectCutoffImpactInput", "ProjectCutoffUpdateInput")
)
def test_cutoff_requires_timezone_and_preserves_offset(kind: str) -> None:
    model = getattr(projects, kind)
    field = "proposed_cutoff_at" if kind == "ProjectCutoffImpactInput" else "cutoff_at"
    payload: dict[str, object] = {"project_id": "p"}
    if kind == "ProjectCreateInput":
        payload["name"] = "Project"
    if kind == "ProjectCutoffUpdateInput":
        payload.update(expected_revision=0, expected_impact_digest="x" * 64)
    assert (
        model.model_validate({**payload, field: "2026-10-12T09:00:00+09:00"}).model_dump(
            mode="json"
        )[field]
        == "2026-10-12T09:00:00+09:00"
    )
    with pytest.raises(ValidationError):
        model.model_validate({**payload, field: "2026-10-12T09:00:00"})


def test_create_string_bounds_and_metadata_none_versus_empty() -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "name": "Project",
        "cutoff_at": "2026-10-12T00:00:00Z",
    }
    for field, limit in (
        ("project_id", 160),
        ("name", 500),
        ("description", 10000),
        ("overlay", 160),
        ("policy_binding_ref", 160),
    ):
        assert projects.ProjectCreateInput.model_validate({**payload, field: "x" * limit})
        with pytest.raises(ValidationError):
            projects.ProjectCreateInput.model_validate({**payload, field: "x" * (limit + 1)})
        if field != "description":
            with pytest.raises(ValidationError):
                projects.ProjectCreateInput.model_validate({**payload, field: ""})
    value = projects.ProjectMetadataUpdateInput(project_id="p", expected_revision=0)
    assert value.name is None and value.description is None
    assert (
        projects.ProjectMetadataUpdateInput(
            project_id="p", expected_revision=0, description=""
        ).description
        == ""
    )
    with pytest.raises(ValidationError):
        projects.ProjectMetadataUpdateInput(project_id="p", expected_revision=0, name="")


def test_role_defaults_tag_order_and_policy_json() -> None:
    payload = {"project_id": "p", "expected_revision": 0, "actor_id": "a", "role": "reader"}
    role = projects.ProjectRoleAssignInput.model_validate(payload)
    assert role.scope == "PROJECT" and role.organization_id is None and role.authority_tags == ()
    role = projects.ProjectRoleAssignInput.model_validate(
        {**payload, "authority_tags": ["z", "a", "z"]}
    )
    assert role.authority_tags == ("z", "a", "z")
    policy = {"allow": False, "nested": [None, {"limit": 2}]}
    assert (
        projects.ProjectPolicyUpdateInput.model_validate(
            {"project_id": "p", "expected_revision": 0, "payload": policy}
        ).model_dump(mode="json")["payload"]
        == policy
    )


def test_cutoff_digest_keeps_exact_length_without_new_hex_restriction() -> None:
    payload = {"project_id": "p", "expected_revision": 0, "cutoff_at": "2026-10-12T00:00:00Z"}
    assert projects.ProjectCutoffUpdateInput.model_validate(
        {**payload, "expected_impact_digest": "z" * 64}
    )
    for size in (63, 65):
        with pytest.raises(ValidationError):
            projects.ProjectCutoffUpdateInput.model_validate(
                {**payload, "expected_impact_digest": "z" * size}
            )
