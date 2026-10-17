"""Pure scope builders bind provenance and digests without deciding access."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from thoth.application.services import resource_scope_records as records
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.governance import ProjectPolicy
from thoth.domain.project import Project
from thoth.domain.resource_scope import (
    ResourceIntakeBasis,
    ResourceScopeBody,
    ResourceScopeReceipt,
    ResourceScopeRecord,
    StagedResourceScope,
    seal_resource_scope,
)

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures/resource_scope_records_golden.json"


def golden(name: str) -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))[name]


def basis(name: str = "explicit_intake") -> ResourceIntakeBasis:
    data = [
        value
        for label, value in golden(name)["outputs"]
        if label == "ResourceScopeService.prepare_intake"
    ][-1]
    return ResourceIntakeBasis.model_validate(data)


def write(name: str, index: int) -> tuple[ResourceScopeRecord, ResourceScopeReceipt]:
    record, receipt, _ = golden(name)["scope_writes"][index]
    return ResourceScopeRecord.model_validate(record), ResourceScopeReceipt.model_validate(receipt)


def test_policy_digest_ignores_stored_claim_and_binds_project_and_payload() -> None:
    policy = ProjectPolicy(
        policy_id="policy:unit",
        project_id="project:unit",
        version=1,
        payload={"allow": True},
        policy_digest="f" * 64,
        created_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    before = policy.model_dump_json()
    expected = "1821275d5c65838892771ac09550bc0c6d6d81caa71894b872f85498a3132b92"
    assert records.policy_content_digest("project:unit", policy) == expected
    assert records.policy_content_digest("project:other", policy) != expected
    assert (
        records.policy_content_digest(
            "project:unit", policy.model_copy(update={"payload": {"allow": False}})
        )
        != expected
    )
    assert policy.model_dump_json() == before


def test_intake_identity_excludes_parent_and_actor_judgments() -> None:
    original = basis()
    project = Project(
        project_id=original.project_id,
        name="fixture",
        cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
        overlay="default",
        policy_binding_ref=original.policy_id,
        revision=original.project_revision,
    )
    policy = ProjectPolicy(
        policy_id=original.policy_id,
        project_id=original.project_id,
        version=1,
        payload={},
        policy_digest=original.policy_digest,
        created_at=original.prepared_at,
    )
    fields = records.intake_identity_fields(original.project_id, project, policy, original.template)
    assert set(fields) == {
        "project_id",
        "project_revision",
        "policy_id",
        "policy_digest",
        "template",
    }
    assert fields["project_revision"] == original.project_revision
    assert fields["template"] is original.template
    assert "parent_digests" not in fields and "principal_digest" not in fields


@pytest.mark.parametrize("name", ["explicit_intake", "configured_intake"])
def test_initial_fields_reproduce_original_record_without_owning_ids_or_clock(name: str) -> None:
    value = [v for label, v in golden(name)["outputs"] if label == "ResourceScopeService.stage"][-1]
    original = StagedResourceScope.model_validate(value)
    before = original.basis.model_dump_json()
    fields = records.initial_scope_fields(original.record.resource_ref, original.basis)
    assert not {"scope_id", "receipt_ref", "created_at", "access_basis"}.intersection(fields)
    body = ResourceScopeBody(
        access_basis=original.record.access_basis,
        **original.basis.template.model_dump(mode="python"),
        scope_id=original.record.scope_id,
        **fields,
        receipt_ref=original.record.receipt_ref,
        created_at=original.record.created_at,
    )
    assert (
        seal_resource_scope(body) == original.record and original.basis.model_dump_json() == before
    )


@pytest.mark.parametrize(
    "name,index",
    [("explicit_intake", 0), ("grant_revoke", 1), ("grant_revoke", 2), ("owner_transfer", -1)],
)
def test_receipt_and_static_wrapper_match_original(name: str, index: int) -> None:
    record, receipt = write(name, index)
    before = record.model_dump_json()
    assert records.scope_receipt(record) == receipt
    wrapper = vars(ResourceScopeService)["_receipt"]
    assert isinstance(wrapper, staticmethod)
    typed_wrapper = cast(Callable[[ResourceScopeRecord], ResourceScopeReceipt], wrapper)
    assert typed_wrapper(record) == receipt
    assert record.model_dump_json() == before


def test_pending_source_override_preserves_all_other_fields() -> None:
    expected = next(
        ResourceScopeRecord.model_validate(r)
        for r, _, _ in golden("pending_lineage")["scope_writes"]
        if r["access_basis"] == "PENDING_SOURCES"
    )
    staged = seal_resource_scope(
        ResourceScopeBody.model_validate(
            {
                **expected.model_dump(mode="python", exclude={"record_digest"}),
                "access_basis": "DERIVED_PARENTS",
                "reason": "apply the explicit ingestion ownership scope",
            }
        )
    )
    before = staged.model_dump_json()
    assert records.pending_source_record(staged) == expected and staged.model_dump_json() == before


def test_assignment_changes_reason_and_seal_only() -> None:
    expected, _ = write("legacy_assignment", -1)
    staged = seal_resource_scope(
        ResourceScopeBody.model_validate(
            {
                **expected.model_dump(mode="python", exclude={"record_digest"}),
                "reason": "apply the explicit ingestion ownership scope",
            }
        )
    )
    before = staged.model_dump_json()
    assert records.assigned_scope_record(staged, expected.reason) == expected
    assert staged.model_dump_json() == before


@pytest.mark.parametrize(
    "event,authenticated",
    [("GRANTED", False), ("GRANTED", True), ("REVOKED", True), ("LINEAGE_EXTENDED", True)],
)
def test_change_fields_preserve_event_visibility_and_supplied_actor(
    event: str, authenticated: bool
) -> None:
    current, _ = write("explicit_intake", 0)
    actor = (
        AuthenticatedActorContext(
            actor_id="human:fixture",
            session_id="session:fixture",
            project_id=current.project_id,
            role_assignment_id="role:fixture",
            role="resource-operator",
            capabilities=("READ", "WRITE"),
            data_scopes=("PROJECT",),
        )
        if authenticated
        else None
    )
    before = current.model_dump_json()
    fields = records.change_fields(current, actor, event, current.grants)
    assert fields["visibility"] == ("EXPLICIT_GRANT" if event == "GRANTED" else "WORKSTREAM")
    assert fields["actor_ref"] == ("human:fixture" if authenticated else "local:operator")
    assert fields["session_ref"] == ("session:fixture" if authenticated else None)
    assert (
        fields["previous_digest"] == current.record_digest
        and fields["revision"] == current.revision + 1
    )
    assert not {"basis_digest", "receipt_ref", "created_at"}.intersection(fields)
    assert current.model_dump_json() == before


def test_change_basis_digest_matches_original_and_binds_previous_record() -> None:
    initial, _ = write("grant_revoke", 0)
    granted, _ = write("grant_revoke", 1)
    original_basis = basis("grant_revoke")
    actual = records.change_basis_digest(
        original_basis.policy_digest, original_basis.principal_digest, initial.record_digest
    )
    assert actual == granted.basis_digest
    assert (
        records.change_basis_digest(
            original_basis.policy_digest, original_basis.principal_digest, "0" * 64
        )
        != actual
    )
