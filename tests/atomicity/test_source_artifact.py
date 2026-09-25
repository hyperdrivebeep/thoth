from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import (
    AllowedRow,
    assert_phase_delta,
    failed_command_allowances,
    snapshot,
)
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.cleanup import CleanupUsage
from thoth.domain.control_record import ControlRecord
from thoth.protocol.deferred import current_operation


@pytest.mark.parametrize(
    "stage",
    [
        "after_resource_scope",
        "after_ingestion",
        "after_source_projection",
        "after_project_projection",
        "after_receipts",
    ],
)
async def test_source_checkpoint_failure_has_no_search_or_current_source_after_reopen(
    tmp_path: Path, stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(point: str) -> None:
        if point == stage:
            raise RuntimeError("source checkpoint fault")

    runtime = create_runtime(tmp_path, acquisition_fault_injector=fail)
    project = "project:source-atomicity"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "project",
                {"project_id": project, "name": "Sources", "cutoff_at": "2026-09-01T00:00:00Z"},
            )
        )
    )
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    raw = "# Original\nA controlled observation."
    (inbox / "source.md").write_text(raw, encoding="utf-8")
    command = request(
        "project/source/connect",
        "fault",
        {
            "project_id": project,
            "relative_path": "source.md",
            "media_type": "text/markdown",
            "authority": "OFFICIAL",
            "cutoff_state": "ELIGIBLE",
        },
    )
    before = snapshot(runtime.ledger.engine)
    witnessed: list[tuple[str, ControlRecord]] = []
    original_create = ControlRecordService.create

    def observe(service: ControlRecordService, **kwargs: Any) -> ControlRecord:
        record = original_create(service, **kwargs)
        operation = current_operation.get()
        assert operation is not None and operation.project_id == project
        assert operation.method == command.method and operation.idempotency_key == "fault"
        witnessed.append((operation.operation_id, record))
        return record

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ControlRecordService, "create", observe)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert len(witnessed) == 4
        io = [record for _, record in witnessed if record.record_type == "IO"]
        cleanup = [
            (operation, record)
            for operation, record in witnessed
            if record.record_type == "CLEANUP_USAGE"
        ]
        assert [record.payload["phase"] for record in io] == ["DISCOVER", "FETCH"]
        assert all(
            record.state == "RETURNED"
            and record.payload["timeout_ms"] == 120000
            and record.payload["remote_stop"] == "UNKNOWN"
            for record in io
        )
        assert len(cleanup) == 2 and [record.state for _, record in cleanup] == [
            "PENDING",
            "COMPLETED",
        ]
        assert cleanup[1][1].supersedes_digest == cleanup[0][1].record_digest
        for operation, record in witnessed:
            assert record.namespace == "CONNECTOR" and record.project_id == project
            assert record.canonical_truth is False
            assert record.record_digest == domain_digest(
                f"{record.namespace}_{record.record_type}",
                "1.0.0",
                canonical_payload(
                    record.model_dump(
                        exclude={"record_digest", "canonical_truth", "schema_version"}
                    )
                ),
            )
            if record.record_type == "CLEANUP_USAGE":
                usage = CleanupUsage.model_validate(record.payload)
                assert usage.parent_operation_id == operation and usage.limit_ms == 2000
                assert usage.connector_id == "local-file-upload" and usage.remote_stop == "UNKNOWN"
                assert record.record_id == f"cleanup:{operation}:{usage.connector_run_id}"
            allowed += (
                AllowedRow(
                    "control_records",
                    {
                        "control_revision_id": record.control_revision_id,
                        "project_id": project,
                        "record_id": record.record_id,
                        "record_digest": record.record_digest,
                        "namespace": record.namespace,
                        "record_type": record.record_type,
                        "version": record.version,
                        "supersedes_digest": record.supersedes_digest,
                    },
                    "added",
                    {"state": (record.state,)},
                ),
            )
        assert_phase_delta(before, snapshot(runtime.ledger.engine), allowed)
        assert (inbox / "source.md").read_text(encoding="utf-8") == raw
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        sources = value(
            await reopened.bus.query(
                request("project/source/list", "sources", {"project_id": project})
            )
        )
        assert sources["artifacts"] == [] and sources["bindings"] == []
        evidence = value(
            await reopened.bus.query(request("evidence/list", "evidence", {"project_id": project}))
        )
        assert evidence["spans"] == []
        connected = value(
            await reopened.bus.dispatch(
                request("project/source/connect", "retry", dict(command.params.input))
            )
        )
        assert connected["evidence_count"] > 0
        after = snapshot(reopened.ledger.engine)
        assert after["structural_fts"]
    finally:
        reopened.close()
