"""Execution input schemas, compatibility names and validation behavior before extraction."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import executions_full

SCHEMAS = cast(
    dict[str, object],
    json.loads((Path(__file__).parents[2] / "fixtures/executions_input_schemas.json").read_bytes()),
)


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_original_schema_and_compatibility_class_identity(name: str) -> None:
    owner = importlib.import_module(executions_full.ExecutionListInput.__module__)
    model = getattr(owner, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert getattr(executions_full, name) is model
    assert model.model_json_schema() == SCHEMAS[name]


def test_project_and_list_bounds_defaults_extra_and_frozen() -> None:
    value = executions_full.ExecutionListInput(project_id="p")
    assert value.model_dump(mode="json") == {
        "project_id": "p",
        "object_id": None,
        "plan_id": None,
        "plan_state": None,
    }
    assert executions_full.ProjectInput(project_id="p" * 160).project_id == "p" * 160
    for project in ("", "p" * 161):
        with pytest.raises(ValidationError):
            executions_full.ProjectInput(project_id=project)
    with pytest.raises(ValidationError) as extra:
        executions_full.ExecutionListInput.model_validate({"project_id": "p", "unexpected": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        value.project_id = "changed"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"
    for field, limit in (("object_id", 160), ("plan_id", 160), ("plan_state", 80)):
        assert executions_full.ExecutionListInput.model_validate(
            {"project_id": "p", field: "x" * limit}
        )
        with pytest.raises(ValidationError):
            executions_full.ExecutionListInput.model_validate(
                {"project_id": "p", field: "x" * (limit + 1)}
            )


def test_start_digest_length_and_required_fields() -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "plan_id": "plan",
        "plan_revision_digest": "z" * 64,
        "execution_profile_ref": "profile",
        "expected_working_head_digest": "x" * 64,
    }
    assert executions_full.StartInput.model_validate(payload).model_dump(mode="json") == payload
    for field in ("plan_revision_digest", "expected_working_head_digest"):
        for size in (63, 65):
            with pytest.raises(ValidationError):
                executions_full.StartInput.model_validate({**payload, field: "z" * size})
    for field in payload:
        with pytest.raises(ValidationError):
            executions_full.StartInput.model_validate(
                {k: v for k, v in payload.items() if k != field}
            )


def test_revision_bound_retry_and_reason_limits() -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "plan_execution_id": "e",
        "expected_execution_revision": 0,
        "step_id": "s",
        "failed_attempt_id": "a",
        "retry_reason": "r" * 2000,
    }
    assert executions_full.RetryInput.model_validate(payload).expected_execution_revision == 0
    for patch in (
        {"expected_execution_revision": -1},
        {"retry_reason": ""},
        {"retry_reason": "r" * 2001},
    ):
        with pytest.raises(ValidationError):
            executions_full.RetryInput.model_validate({**payload, **patch})


@pytest.mark.parametrize("state", ("COMPLETE", "PARTIAL", "MISSING", "CORRUPT", "NOT_APPLICABLE"))
def test_observation_vocabulary_and_reference_order(state: str) -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "attempt_id": "a",
        "observation_refs": ["z", "a"],
        "completeness": state,
        "evidence_refs": [],
        "expected_execution_revision": 0,
    }
    value = executions_full.ObservationLinkInput.model_validate(payload)
    assert value.observation_refs == ("z", "a") and value.evidence_refs == ()
    with pytest.raises(ValidationError):
        executions_full.ObservationLinkInput.model_validate(
            {**payload, "completeness": state.lower()}
        )


def test_invalidate_optional_clearing_and_json_payload() -> None:
    payload: dict[str, object] = {
        "project_id": "p",
        "plan_execution_id": "e",
        "cause_revision_ref": "revision",
        "impact_refs": ["b", "a"],
    }
    value = executions_full.InvalidateInput.model_validate(payload)
    assert value.model_dump(mode="json") == {
        **payload,
        "clear_pending_attempt_id": None,
        "expected_execution_revision": None,
        "reason": None,
        "input_difference": None,
    }
    updated = executions_full.InvalidateInput.model_validate(
        {
            **payload,
            "clear_pending_attempt_id": "a",
            "expected_execution_revision": 0,
            "reason": "repeat with a new input",
            "input_difference": {"nested": [1, None, {"x": False}]},
        }
    )
    assert updated.model_dump(mode="json")["input_difference"] == {
        "nested": [1, None, {"x": False}]
    }
    for patch in (
        {"clear_pending_attempt_id": ""},
        {"expected_execution_revision": -1},
        {"reason": ""},
    ):
        with pytest.raises(ValidationError):
            executions_full.InvalidateInput.model_validate({**payload, **patch})
