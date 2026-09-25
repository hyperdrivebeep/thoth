from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.atomicity.outcome_helpers import prepare_outcome
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.domain.canonical import canonical_payload, domain_digest


@pytest.mark.parametrize("owner", ["object", "outcome"])
async def test_audit_seal_uses_the_timestamp_it_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    runtime, ctx = await (
        setup(tmp_path) if owner == "object" else prepare_outcome(tmp_path, create_series=False)
    )
    try:
        ticks = 0

        def advancing(clock: SystemClock) -> datetime:
            nonlocal ticks
            ticks += 1
            return datetime(2026, 9, 14, tzinfo=UTC) + timedelta(seconds=ticks)

        if owner == "object":
            store = SqliteDecisionObjectStore(runtime.ledger.engine)
            before = {row.audit_id for row in store.list_audit(ctx["project"], ctx["object"])}
            current = value(
                await runtime.bus.dispatch(
                    request(
                        "object/read",
                        "object",
                        {"project_id": ctx["project"], "object_id": ctx["object"]},
                    )
                )
            )["object"]
            command = request(
                "object/frame/revise",
                "change",
                {
                    "project_id": ctx["project"],
                    "object_id": ctx["object"],
                    "expected_revision_digest": current["revision_digest"],
                    "frame_patch": {"purpose_statement": "A current scoped frame"},
                    "evidence_refs": ctx["spans"],
                    "reason": "update",
                },
            )
        else:
            before = {
                row.audit_id
                for row in SqliteOutcomeStore(runtime.ledger.engine).list_audit(
                    ctx["project"], None
                )
            }
            command = request("outcome/series/create", "series", ctx["series_input"])
        with monkeypatch.context() as patch:
            patch.setattr(SystemClock, "now", advancing)
            value(await runtime.bus.dispatch(command))
        records = (
            SqliteDecisionObjectStore(runtime.ledger.engine).list_audit(
                ctx["project"], ctx["object"]
            )
            if owner == "object"
            else SqliteOutcomeStore(runtime.ledger.engine).list_audit(ctx["project"], None)
        )
        changed = [row for row in records if row.audit_id not in before]
        assert changed
        for record in changed:
            body = record.model_dump(
                mode="python", exclude={"audit_id", "event_digest", "schema_version"}
            )
            assert record.event_digest == domain_digest(
                f"{owner.upper()}_AUDIT", "1.0.0", canonical_payload(body)
            )
    finally:
        runtime.close()
