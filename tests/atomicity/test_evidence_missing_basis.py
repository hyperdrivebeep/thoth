from pathlib import Path

import pytest
from tests.atomicity.evidence_helpers import evidence_service
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import link_input, setup

from thoth.domain.evidence_graph import EvidenceSourceRecord


@pytest.mark.parametrize("fault", ["missing_history", "foreign_history", "missing_current"])
async def test_revalidation_cannot_repair_missing_or_foreign_lineage_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        link = value(
            await runtime.bus.dispatch(request("evidence/link/propose", "link", link_input(ctx)))
        )["evidence"]
        service = evidence_service(runtime)
        store = service._store  # pyright: ignore[reportPrivateUsage]
        original = store.read_source

        def altered(identifier: str) -> EvidenceSourceRecord | None:
            source = original(identifier)
            if identifier != ctx["source"]:
                return source
            return (
                None
                if fault == "missing_history" or source is None
                else source.model_copy(update={"project_id": "project:foreign"})
            )

        command = request(
            "evidence/revalidate",
            "fault",
            {"project_id": ctx["project"], "evidence_id": link["evidence_id"]},
        )
        before = snapshot(runtime.ledger.engine)

        def missing_current(artifact_id: str) -> None:
            return None

        with monkeypatch.context() as patch:
            if fault == "missing_current":
                patch.setattr(store, "read_source_by_artifact", missing_current)
            else:
                patch.setattr(store, "read_source", altered)
            response = await runtime.bus.dispatch(command)
        assert response.error is not None
        assert_phase_delta(
            before,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, command),
        )
    finally:
        runtime.close()
