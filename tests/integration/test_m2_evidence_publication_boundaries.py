"""Normal research entry rolls back every evidence-publication failure boundary."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value

from thoth.adapters.storage.acquisition_trace import SqliteEvidenceUnitOfWork
from thoth.adapters.storage.schema import (
    evidence_audit,
    evidence_leads,
    evidence_links,
    evidence_observations,
)
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.acquisition import EvidenceAtomicCommit


@pytest.mark.parametrize(
    "point", ("after_observation", "after_lead", "after_claim_candidate", "after_audit")
)
async def test_research_entry_rolls_back_evidence_phase_and_preserves_source_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    fired: list[str] = []
    rolled_back: list[EvidenceAtomicCommit] = []
    active: list[EvidenceAtomicCommit] = []
    enabled = True

    def inject_fault(stage: str) -> None:
        if stage == point and enabled:
            pending = active[0]
            assert pending.lead is not None
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    connection.execute(
                        select(evidence_observations.c.observation_id).where(
                            evidence_observations.c.observation_id
                            == pending.observation.observation_id
                        )
                    ).scalar_one()
                    == pending.observation.observation_id
                )
                if point != "after_observation":
                    assert (
                        connection.execute(
                            select(evidence_leads.c.lead_id).where(
                                evidence_leads.c.lead_id == pending.lead.lead_id
                            )
                        ).scalar_one()
                        == pending.lead.lead_id
                    )
                if point in {"after_claim_candidate", "after_audit"}:
                    assert (
                        connection.execute(
                            select(evidence_links.c.evidence_id).where(
                                evidence_links.c.evidence_id == pending.claim_candidate.evidence_id
                            )
                        ).scalar_one()
                        == pending.claim_candidate.evidence_id
                    )
                if point == "after_audit":
                    expected = {record.audit_id for record in pending.audit_records}
                    assert (
                        set(
                            connection.execute(
                                select(evidence_audit.c.audit_id).where(
                                    evidence_audit.c.audit_id.in_(expected)
                                )
                            ).scalars()
                        )
                        == expected
                    )
            fired.append(stage)
            raise RuntimeError("M2 evidence publication fault")

    runtime, connector, project = await prepare_thread(
        tmp_path, allow_connector=True, evidence_fault_injector=inject_fault
    )
    original = SqliteEvidenceUnitOfWork.commit

    def observed_commit(store: SqliteEvidenceUnitOfWork, pending: EvidenceAtomicCommit) -> None:
        before = snapshot(runtime.ledger.engine)
        active[:] = [pending]
        try:
            original(store, pending)
        except RuntimeError as exc:
            assert str(exc) == "M2 evidence publication fault"
            assert_phase_delta(before, snapshot(runtime.ledger.engine))
            rolled_back.append(pending)
            raise

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteEvidenceUnitOfWork, "commit", observed_commit)
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "m2-publication-fault",
                        {"project_id": project, "thread_id": f"thread:{project}"},
                    )
                )
            )
        acquisition = cast(dict[str, JsonValue], result["autonomous_acquisition"])
        assert acquisition["terminal_state"] == "FAILED"
        assert acquisition["reanalysis_performed"] is False
        assert fired == [point] and len(rolled_back) == 1
        assert connector.discover_count == connector.fetch_count == 2
        saved = snapshot(runtime.ledger.engine)
        # Acquisition's earlier, successful source checkpoint remains available.
        assert len(saved["artifacts"]) == 2 and saved["structural_fts"]
        if point == "after_audit":
            enabled = False
            value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "m2-after-fault-normal-input",
                        {"project_id": project, "thread_id": f"thread:{project}"},
                    )
                )
            )
            recovered = snapshot(runtime.ledger.engine)
            assert len(recovered["semantic_revisions"]) > len(saved["semantic_revisions"])
            saved = recovered
    finally:
        runtime.close()

    reopened = create_runtime(tmp_path / "allowed")
    try:
        assert_phase_delta(saved, snapshot(reopened.ledger.engine))
        pending = rolled_back[0]
        identifiers = [pending.observation.observation_id, pending.claim_candidate.evidence_id]
        assert pending.lead is not None
        identifiers.append(pending.lead.lead_id)
        identifiers.extend(record.audit_id for record in pending.audit_records)
        serialized = "\n".join(row for rows in saved.values() for row in rows)
        assert all(identifier not in serialized for identifier in identifiers)
    finally:
        reopened.close()
