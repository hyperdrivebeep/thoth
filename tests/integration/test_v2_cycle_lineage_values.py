"""Normal v2 execution publishes request and record lineage through the real DAG writer."""

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.domain.receipt import Receipt
from thoth.domain.receipt_dag import ReceiptDagManifest, ReceiptNodeKind
from thoth.domain.research_execution import research_work


async def test_v2_cycle_passes_request_and_record_refs_to_real_dag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    captured: list[tuple[str, ...]] = []
    original = ReceiptDagService.record_cycle

    def record(
        self: ReceiptDagService,
        project_id: str,
        values: dict[ReceiptNodeKind, tuple[str, ...]],
        receipt: Receipt | None = None,
    ) -> ReceiptDagManifest:
        work = research_work.get()
        assert work is not None and receipt is not None
        assert project_id == work.request_ref.project_id == "p"
        expected = (
            receipt.receipt_id,
            work.request_ref.revision_digest,
            *(ref.revision_digest for ref in work.record_refs),
        )
        assert work.record_refs
        assert values[ReceiptNodeKind.REVISION] == expected
        captured.append(expected)
        return original(self, project_id, values, receipt)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ReceiptDagService, "record_cycle", record)
            accepted = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/start",
                        "lineage-v2",
                        {
                            "project_id": "p",
                            "problem": "LAB-42 지연 조건",
                            "contract_version": 2,
                        },
                    )
                )
            )
            await runtime.bus.drain()
        assert captured
        result = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "lineage-read",
                    {
                        "project_id": "p",
                        "thread_id": accepted["thread_id"],
                        "view": "FULL",
                    },
                )
            )
        )
        assert result["operation_state"] == "SUCCEEDED"
    finally:
        runtime.close()
