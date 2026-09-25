from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid,post_hoc", [(False, False), (True, False), (False, True)])
async def test_v2_normal_input_preserves_execution_validity_and_next_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: bool, post_hoc: bool
):
    runtime, sandbox, project, thread, _ = await prepare_measurement(
        tmp_path,
        monkeypatch,
        corrupt_unit=invalid,
        prespecification_state="POST_HOC" if post_hoc else "A_PRIORI",
        choose_read_after_test=True,
    )
    auxiliary = ControlledResearchModel()
    seen_contexts: list[str] = []
    existing = A04R2Model.structured

    async def combined(self: A04R2Model, call: ModelRequest[BaseModel]) -> ModelResult[BaseModel]:
        seen_contexts.append(call.context_pack.model_dump_json())
        if call.role in {
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.SOURCE_PLANNER,
            ModelRole.HYPOTHESIS_REVIEWER,
        }:
            return await auxiliary.structured(call)
        return await existing(self, call)

    monkeypatch.setattr(A04R2Model, "structured", combined)
    monkeypatch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "research-measurement",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "contract_version": 2,
                        "instruction": "Use the sealed measurement protocol. Preserve uncertainty.",
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(accepted["operation_id"])
        assert operation is not None and operation.state.value == "SUCCEEDED", operation
        result = cast(dict[str, Any], operation.result)
        assert result is not None and "r2_closed_loop" in result, result
        lifecycle = result["r2_closed_loop"]["test_lifecycle"]
        assert len(sandbox.seen_specs) == 1
        assert lifecycle["semantic_truth_certified"] is False
        assert lifecycle["official_criterion_disposition"] == "NOT_ASSESSED"
        assessments = lifecycle["assessments"]
        if invalid:
            assert all(item["test_validity"] != "VALID" for item in assessments)
        if invalid or post_hoc:
            assert all(not item["substantive_update_allowed"] for item in assessments)
        else:
            assert {item["prediction_fit"] for item in assessments} == {"MATCH", "MISMATCH"}
        learning = result["post_execution_learning"]
        assert learning["state"] in {"COMMITTED", "HELD"}, learning
        assert learning["memory_revision_refs"] and learning["review_receipt_refs"]
        outcome_ref = learning["basis"]["execution"]["outcome_revision_ref"]
        outcome_revision = runtime.ledger.read_revision_by_digest(
            project, outcome_ref["revision_digest"]
        )
        assert outcome_revision is not None
        assert outcome_revision.entity_id == result["r2_closed_loop"]["outcome"]["outcome_id"]
        assert any(
            receipt["source_revision_ref"] == outcome_ref["revision_digest"]
            for receipt in learning["promotion"]["receipts"]
        )
        assert any(
            memory["owner_revision_ref"] == outcome_ref["revision_digest"]
            for memory in learning["promotion"]["committed"]
        ), learning
        assert result["improvement_observation"]["trigger_state"] == "NOT_TRIGGERED"
        assert result["improvement_observation"]["reason_code"] == "NO_FAILURE"
        assert "hypothesis_review" in result
        if not invalid and not post_hoc:
            learned = next(
                memory
                for memory in learning["promotion"]["committed"]
                if memory["owner_revision_ref"] == outcome_ref["revision_digest"]
            )
            before = len(seen_contexts)
            followup = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "next-question",
                        {
                            "project_id": project,
                            "thread_id": thread,
                            "contract_version": 2,
                            "instruction": "Recall the recorded process observation "
                            "and outcome limitations.",
                        },
                    )
                )
            )
            await runtime.bus.drain()
            following = runtime.bus.read_operation(str(followup["operation_id"]))
            assert following is not None and following.state.value == "SUCCEEDED"
            assert len(sandbox.seen_specs) == 1
            assert any(learned["memory_revision_id"] in text for text in seen_contexts[before:])
            next_result = cast(dict[str, Any], following.result)
            assert any(
                memory["owner_revision_ref"] == outcome_ref["revision_digest"]
                for memory in next_result["authorized_project_memory"]["included"]
            )
    finally:
        runtime.close()
