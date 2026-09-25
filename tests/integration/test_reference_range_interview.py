"""Reference requests enter through the normal Thread and retain a bounded inquiry."""

import json
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import prepare_thread

from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.research_identity import decode_research


async def test_normal_thread_asks_for_missing_reference_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    thread = f"thread:{project}"
    projection_differences: list[object] = []
    add = SqliteCriterionContractStore.add_contract

    def observe(store: SqliteCriterionContractStore, record: CriterionContractRecord) -> None:
        add(store, record)
        revision = runtime.ledger.read_revision_by_digest(project, record.revision_digest)
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        decoded = decode_research(revision, snapshot).record
        projected = CriterionContractRecord.model_validate_json(record.model_dump_json())
        if projected != decoded:
            left = projected.model_dump(mode="json")
            right = {} if decoded is None else decoded.model_dump(mode="json")
            projection_differences.append(
                {key: (left[key], right.get(key)) for key in left if left[key] != right.get(key)}
            )
            (tmp_path / "projection-differences.json").write_text(
                json.dumps(projection_differences, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    monkeypatch.setattr(SqliteCriterionContractStore, "add_contract", observe)
    try:
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "reference-source-list", {"project_id": project})
            )
        )
        span_ids = [item["span_id"] for item in evidence["spans"]]
        compiled = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/compile",
                    "reference-criterion",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "source_span_ids": span_ids,
                        "profile_refs": ["GENERAL_RND"],
                    },
                )
            )
        )["criterion"]
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "ask-reference-condition",
                {
                    "project_id": project,
                    "thread_id": thread,
                    "instruction": "Compare a reference range; ask for missing conditions.",
                    "reference_request": {
                        "criterion_id": compiled["criterion_id"],
                        "expected_revision_digest": compiled["revision_digest"],
                        "lane": "REFERENCE_RANGE_CANDIDATE",
                        "source_refs": span_ids,
                        "calculator_id": "observed-range",
                        "calculator_version": "1.0.0",
                        "target": {
                            "metric_definition": "latency",
                            "formula": "elapsed time",
                            "unit": "ms",
                            "population": "declared test population",
                            "environment": "controlled test environment",
                            "time_window": "trial-v1",
                            "measurement_method": "elapsed-time observation",
                        },
                        "measurements": [],
                    },
                },
            )
        )
        assert response.error is None, (response.error, projection_differences)
        result = value(response)
        inquiry = result["reference_inquiry"]
        assert inquiry["state"] == "NEEDS_INPUT"
        assert "denominator" in {question["field"] for question in inquiry["questions"]}
        assert inquiry["calculation"] is None
        assert inquiry["authorization_state"] == "NOT_AUTHORIZED"
        assert not inquiry["evaluator_input_allowed"]
        current = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/read",
                    "read-reference-inquiry",
                    {
                        "project_id": project,
                        "criterion_id": compiled["criterion_id"],
                    },
                )
            )
        )["criterion"]
        assert current["reference_inquiry"]["inquiry_id"] == inquiry["inquiry_id"]
        assert current["acceptance_rule"] == compiled["acceptance_rule"]
        assert current["usage_authorization"] == compiled["usage_authorization"]
    finally:
        runtime.close()
