from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.application.services import RevisionCommitService
from thoth.apps.runtime import create_runtime
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.protocol.jsonrpc import JsonRpcRequest

ACTOR = ActorRef(actor_id="agent:test", kind=ActorKind.AGENT, role="fixture")


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def _staged(identifier: str, value: str, parents: tuple[str, ...]) -> StagedRevision:
    content: dict[str, object] = {"value": value}
    content_digest = domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content))
    snapshot = EntitySnapshot(
        snapshot_id=f"snapshot:{identifier}",
        project_id="project:revision-rpc",
        entity_type=EntityType.HYPOTHESIS,
        entity_id="portfolio:1",
        schema_version="1.0.0",
        content=content,
        content_digest=content_digest,
    )
    revision_payload: dict[str, object] = {
        "id": identifier,
        "content": content_digest,
        "parents": parents,
    }
    revision = SemanticRevision(
        revision_id=f"revision:{identifier}",
        project_id="project:revision-rpc",
        entity_type=EntityType.HYPOTHESIS,
        entity_id="portfolio:1",
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=parents,
        actor=ACTOR,
        reason=value,
        evidence_refs=(),
        affected_refs=(),
        revision_digest=domain_digest("REVISION", "1.0.0", canonical_payload(revision_payload)),
        created_at=datetime(2026, 8, 30, 10, 0, tzinfo=UTC),
    )
    return StagedRevision(snapshot=snapshot, revision=revision)


def _commit(
    service: RevisionCommitService,
    staged: StagedRevision,
    expected: dict[str, str],
) -> None:
    service.commit(
        RevisionChangeSet(
            changeset_id=f"changeset:{staged.revision.revision_id}",
            project_id="project:revision-rpc",
            expected_heads=expected,
            staged_revisions=(staged,),
            impact_plan=ImpactPropagationPlan(),
            actor=ACTOR,
            reason=staged.revision.reason,
        )
    )


@pytest.mark.asyncio
async def test_revision_history_compare_and_restore_are_rpc_reachable(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    created = await runtime.bus.dispatch(
        _rpc(
            "project/create",
            "revision-project",
            {
                "project_id": "project:revision-rpc",
                "name": "Revision fixture",
                "cutoff_at": "2026-08-31T00:00:00Z",
            },
        )
    )
    assert created.error is None
    service = RevisionCommitService(
        runtime.ledger,
        SystemClock(),
        UuidIdGenerator(),
        policy_version="policy:test",
    )
    first = _staged("1", "first", ())
    _commit(service, first, {})
    second = _staged("2", "second", (first.revision.revision_digest,))
    _commit(
        service,
        second,
        {"HYPOTHESIS:portfolio:1": first.revision.revision_digest},
    )
    base: dict[str, object] = {
        "project_id": "project:revision-rpc",
        "entity_type": "HYPOTHESIS",
        "entity_id": "portfolio:1",
    }
    try:
        history = await runtime.bus.dispatch(_rpc("revision/history/read", "history", base))
        compare = await runtime.bus.dispatch(
            _rpc(
                "revision/compare",
                "compare",
                {
                    **base,
                    "left_revision_id": "revision:1",
                    "right_revision_id": "revision:2",
                },
            )
        )
        restore_command = _rpc(
            "revision/restore",
            "restore",
            {
                **base,
                "selected_revision_id": "revision:1",
                "expected_current_head": second.revision.revision_digest,
                "reason": "restore the first known state",
            },
        )
        before_restore = snapshot(runtime.ledger.engine)
        restore = await runtime.bus.dispatch(restore_command)
        assert (
            runtime.ledger.read_heads(str(base["project_id"]))["HYPOTHESIS:portfolio:1"]
            == second.revision.revision_digest
        )
        assert_phase_delta(
            before_restore,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, restore_command),
        )
    finally:
        runtime.close()

    assert history.result is not None
    history_value = history.result["value"]
    assert isinstance(history_value, dict)
    revisions = history_value["revisions"]
    assert isinstance(revisions, list)
    assert len(revisions) == 2
    assert compare.result is not None
    compare_value = compare.result["value"]
    assert isinstance(compare_value, dict)
    comparison = compare_value["comparison"]
    assert isinstance(comparison, dict)
    changes = comparison["changes"]
    assert isinstance(changes, list)
    assert any(isinstance(change, dict) and change.get("path") == "/value" for change in changes)
    # An untyped legacy {value: ...} snapshot remains readable/comparable, not restorable.
    assert restore.error is not None
    assert restore.error.data["reason_code"] == "RESTORE_SCHEMA_UNSUPPORTED"
