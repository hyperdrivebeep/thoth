import json
from pathlib import Path
from typing import Any

import pytest
from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import domain_snapshot, fail_after, request, value

from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.services.reference_service import ReferenceService, ReferenceTransition
from thoth.application.services.revision_service import RevisionCommitService
from thoth.apps.runtime import create_runtime
from thoth.domain.reference import ReferenceRequest


@pytest.mark.parametrize("fault", ["projection", "audit", "receipt", "thread"])
async def test_reference_phase_rolls_back_every_domain_table_after_storage_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    async with reference_harness(tmp_path) as h:
        original = ReferenceService.begin
        checked: list[bool] = []
        owners = {
            "projection": (SqliteCriterionContractStore, "add_contract"),
            "audit": (SqliteCriterionContractStore, "append_audit"),
            "receipt": (RevisionCommitService, "commit"),
            "thread": (SqliteThreadStore, "update"),
        }

        def faulty_begin(
            instance: ReferenceService,
            project: str,
            thread: str,
            body: ReferenceRequest,
            **kwargs: Any,
        ) -> ReferenceTransition:
            # Normal research has committed before this boundary. Compare all domain tables
            # at the reference phase boundary; no partially committed reference writes survive.
            before = domain_snapshot(h.runtime.ledger.engine)
            with monkeypatch.context() as patch:
                owner, method = owners[fault]
                fail_after(patch, owner, method)
                with pytest.raises(RuntimeError, match="injected storage failure"):
                    original(instance, project, thread, body, **kwargs)
            assert domain_snapshot(h.runtime.ledger.engine) == before
            checked.append(True)
            raise RuntimeError("verified reference rollback")

        monkeypatch.setattr(ReferenceService, "begin", faulty_begin)
        result = await h.input("fault-reference", reference_request=h.request)
        assert result.error is not None and checked == [True]
        assert (await h.read())["revision_digest"] == h.criterion["revision_digest"]


async def test_reference_duplicate_stale_revision_and_reopen_preserve_history(
    tmp_path: Path,
) -> None:
    async with reference_harness(tmp_path) as h:
        before = domain_snapshot(h.runtime.ledger.engine)
        first = value(await h.input("once", reference_request=h.request))
        committed = domain_snapshot(h.runtime.ledger.engine)
        assert value(await h.input("once", reference_request=h.request)) == first
        assert domain_snapshot(h.runtime.ledger.engine) == committed
        stale = await h.input("stale", reference_request=h.request)
        assert stale.error is not None
        assert "REFERENCE_CRITERION_REVISION_CONFLICT" in str(stale.error)
        after_stale = domain_snapshot(h.runtime.ledger.engine)
        for table in committed:
            if table != "control_records":
                assert after_stale[table] == committed[table], table
        # The outer behavior envelope records the denied attempt, without any model use.
        added = set(after_stale["control_records"]) - set(committed["control_records"])
        assert set(committed["control_records"]) <= set(after_stale["control_records"])
        assert len(added) == 2
        records = [json.loads(json.loads(row)["content_json"]) for row in added]
        assert {record["state"] for record in records} == {"RUNNING", "HELD"}
        assert len({record["record_id"] for record in records}) == 1
        for record in records:
            assert record["namespace"] == "BEHAVIOR_RUNTIME"
            assert record["record_type"] == "EXECUTION"
            assert record["canonical_truth"] is False
            assert record["payload"]["uses"] == []
            assert record["payload"]["semantic_truth_certified"] is False
        committed = after_stale
        # Existing immutable snapshots remain byte-for-byte identical.
        assert set(before["entity_snapshots"]) <= set(committed["entity_snapshots"])
        current = await h.read()
        reopened = create_runtime(tmp_path / "allowed")
        try:
            result = value(
                await reopened.bus.dispatch(
                    request(
                        "criteria/read",
                        "reopen",
                        {
                            "project_id": h.project,
                            "criterion_id": h.criterion["criterion_id"],
                        },
                    )
                )
            )["criterion"]
            assert result == current
            assert domain_snapshot(reopened.ledger.engine) == committed
        finally:
            reopened.close()
