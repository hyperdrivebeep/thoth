"""A counterevidence request records pending work without claiming a completed search."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import creation, setup

OUT = Path(__file__).resolve().parents[2] / "outputs/defect-d2-counterevidence-stage-20261014"


def save(stage: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (stage + "-request-history.json")).write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("stage", ["GROUNDED_CANDIDATE", "DRAFT"])
async def test_counter_request_preserves_stage_and_history(tmp_path: Path, stage: str) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        payload = creation(ctx)
        if stage == "DRAFT":
            payload.update(evidence_refs=[], scope={})
        before = value(
            await runtime.bus.dispatch(request("hypothesis/create", "d2-create", payload))
        )["hypothesis"]
        assert before["development_stage"] == stage
        project, identifier = ctx["project"], before["hypothesis_id"]
        command = request(
            "hypothesis/counterevidence/request",
            "d2-counter",
            {
                "project_id": project,
                "hypothesis_id": identifier,
                "source_scope": ["independent sources"],
            },
        )
        requested = value(await runtime.bus.dispatch(command))
        after = requested["hypothesis"]
        investigation = requested["investigation"]
        after_request = snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(command)) == requested
        assert snapshot(runtime.ledger.engine) == after_request
        page = value(
            await runtime.bus.query(
                request(
                    "revision/timeline/read",
                    "d2-timeline",
                    {
                        "project_id": project,
                        "scope": {"project_id": project},
                        "limit": 25,
                        "contract_version": 2,
                    },
                )
            )
        )
        item = next(
            row
            for row in page["items"]
            if row["record_ref"]["revision_digest"] == after["revision_digest"]
        )
        detail = value(
            await runtime.bus.query(
                request(
                    "revision/timeline/item/read",
                    "d2-detail",
                    {
                        "project_id": project,
                        "scope": {"project_id": project},
                        "record_ref": item["record_ref"],
                        "contract_version": 2,
                    },
                )
            )
        )
        observed = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/read",
                    "d2-investigation",
                    {
                        "project_id": project,
                        "investigation_id": investigation["investigation_id"],
                    },
                )
            )
        )
        current = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "d2-current",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        historical = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "d2-before",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                        "revision_digest": before["revision_digest"],
                    },
                )
            )
        )["hypothesis"]
        summary = detail["item"]["change_summary"]
        evidence = {
            "before": before,
            "requested": requested,
            "history_item": item,
            "history_detail": detail,
            "investigation": observed,
            "historical": historical,
            "replay_exact": True,
        }
        save(stage, evidence)
        assert after["development_stage"] == before["development_stage"]
        assert historical == before and current == after
        assert after["counterevidence_queries"] == [
            *before["counterevidence_queries"],
            investigation["investigation_id"],
        ]
        assert after["empirical_appraisal"] == before["empirical_appraisal"]
        assert observed["investigation"] == investigation
        assert investigation["domain_state"] == "ACTIVE"
        assert investigation["execution_state"] == "IDLE"
        assert investigation["current_wave"] == investigation["observation_count"] == 0
        assert observed["search_intents"] == observed["leads"] == []
        assert summary is not None
        assert not any(line["label"] == "발전 단계" for line in summary["lines"])
        assert "STATUS" not in summary["flags"]
        assert summary["text_diff_recommended"] is False
        assert (
            runtime.ledger.read_heads(project)["HYPOTHESIS:" + identifier]
            == after["revision_digest"]
        )
        receipt = requested["commit"]["receipt"]
        assert after["hypothesis_revision_id"] in receipt["subject_refs"]
        assert any(
            row.receipt_id == receipt["receipt_id"] for row in runtime.ledger.read_receipts(project)
        )
        saved = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == saved
        restored = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/read",
                    "d2-restored",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        assert restored == after
        before_replay = snapshot(reopened.ledger.engine)
        assert value(await reopened.bus.dispatch(command)) == requested
        assert snapshot(reopened.ledger.engine) == before_replay
        restored_detail = value(
            await reopened.bus.query(
                request(
                    "revision/timeline/item/read",
                    "d2-restored-detail",
                    {
                        "project_id": project,
                        "scope": {"project_id": project},
                        "record_ref": item["record_ref"],
                        "contract_version": 2,
                    },
                )
            )
        )
        assert restored_detail["item"]["change_summary"] == summary
        save(stage, {**evidence, "verdict": "PASS", "reopened_exact": True, "restored": restored})
    finally:
        reopened.close()
