from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel

from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_failure import ResearchFailureRecord

TModel = TypeVar("TModel", bound=BaseModel)
SECRET_MARKER = "raw-prompt-secret-must-not-persist"


def canonical_counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "working_heads",
                "semantic_revisions",
                "receipts",
                "memory_records",
                "memory_revision_ledger",
                "memory_transition_receipts",
            )
        }


class FaultAfterCriterionModel(GenericProjectPackModel):
    def __init__(self, workspace: Path) -> None:
        self.database = workspace / "db" / "thoth.sqlite3"
        self.before_failure: dict[str, int] | None = None

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        if request.role == ModelRole.ACTION_PLANNER:
            self.before_failure = canonical_counts(self.database)
            raise RuntimeError(SECRET_MARKER)
        return await super().structured(request)


@pytest.mark.asyncio
async def test_internal_failure_is_generic_atomic_and_persists_only_safe_diagnostics(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "public-fault"
    model = FaultAfterCriterionModel(workspace)

    with pytest.raises(RuntimeError, match="research execution failed") as caught:
        await run_current_hero(
            pack_name="public-demo-membrane",
            workspace=workspace,
            model=model,
            execution_mode="LIVE",
        )

    assert SECRET_MARKER not in str(caught.value)
    database = workspace / "db" / "thoth.sqlite3"
    with sqlite3.connect(database) as connection:
        operation_error = connection.execute(
            "SELECT error_json FROM operations WHERE method = 'thread/input'"
        ).fetchone()
        input_state = connection.execute(
            "SELECT state FROM thread_inputs ORDER BY ordinal DESC LIMIT 1"
        ).fetchone()
        delivery_row = connection.execute(
            "SELECT content_json FROM control_records WHERE namespace = 'RESEARCH_EXECUTION' "
            "AND record_type = 'InputDelivery' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        model_heads = connection.execute(
            "SELECT count(*) FROM working_heads WHERE aggregate_key LIKE 'HYPOTHESIS:%' "
            "OR aggregate_key LIKE 'ACTION:%'"
        ).fetchone()
        failures = connection.execute(
            "SELECT content_json FROM control_records WHERE namespace = 'RESEARCH_EXECUTION' "
            "AND record_type = 'ResearchFailureRecord'"
        ).fetchall()
        execution_records = connection.execute(
            "SELECT content_json FROM control_records WHERE namespace = 'RESEARCH_EXECUTION'"
        ).fetchall()
    assert operation_error is not None
    error = json.loads(str(operation_error[0]))
    assert error["message"] == "research execution failed"
    assert set(error["data"]) == {"reason_code", "failure"}
    assert SECRET_MARKER not in json.dumps(error, sort_keys=True)
    assert (
        input_state is None
    )  # v2 keeps authored input and delivery separately from the legacy queue.
    assert delivery_row is not None
    delivery = json.loads(delivery_row[0])["payload"]
    assert delivery["state"] == "APPLIED" and delivery["first_result_ref"] is not None
    assert model_heads == (0,)  # No failed full Hypothesis/Action publication.

    assert len(failures) == 1
    diagnostic = ResearchFailureRecord.model_validate(json.loads(failures[0][0])["payload"])
    assert diagnostic.primary.model_dump() == {
        "reason_code": "RESEARCH_INTERNAL_ERROR",
        "exception_type": "RuntimeError",
        "origin": "RESEARCH_EXECUTION",
        "detail": None,
    }
    assert diagnostic.secondary is None
    assert diagnostic.last_checkpoint_ref is not None
    assert diagnostic.retry_requires_user_action is True
    assert error["data"]["reason_code"] == diagnostic.primary.reason_code
    assert error["data"]["failure"] == diagnostic.model_dump(mode="json")
    # Checkpoints already published before the fault survive; the failing model
    # and termination publish no additional science or memory revisions.
    assert model.before_failure is not None
    assert model.before_failure["working_heads"] > 0
    assert model.before_failure["semantic_revisions"] > 0
    assert model.before_failure["receipts"] > 0
    assert canonical_counts(database) == model.before_failure
    assert all(SECRET_MARKER not in row[0] for row in execution_records)
    assert not (workspace / "diagnostics" / "internal-failures.jsonl").exists()
