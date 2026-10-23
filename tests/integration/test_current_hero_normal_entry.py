from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import RecordingGenericProjectPackModel

from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis import HypothesisPortfolioV3, HypothesisTableFill


@pytest.mark.asyncio
async def test_current_hero_enters_through_one_normal_thread_input(tmp_path: Path) -> None:
    model = RecordingGenericProjectPackModel()
    result = await run_current_hero(
        pack_name="6g-sandbox-hero",
        workspace=tmp_path / "hero",
        model=model,
        execution_mode="SEALED_REPLAY",
    )
    trace = cast(dict[str, object], result.manifest["normal_entry_trace"])
    assert result.manifest["normal_entry_method"] == "thread/input"
    assert result.manifest["manual_semantic_rpc_assembly"] is False
    assert trace["source"] == "COMMAND_BUS_OPERATION_STORE"
    assert trace["thread_input_count"] == 1
    assert trace["manual_semantic_rpc_count"] == 0
    assert isinstance(trace["trace_digest"], str)
    assert len(trace["trace_digest"]) == 64
    # Bootstrap reads use the read-only query path; one accepted research operation remains.
    assert len(cast(list[str], trace["operation_ids"])) == 1
    assert cast(list[str], trace["methods"])[-1] == "thread/input"
    stages = cast(dict[str, dict[str, object]], result.manifest["stages"])
    assert cast(int, stages["source_cutoff"]["eligible"]) >= 1
    assert cast(int, stages["reasoning"]["hypotheses"]) >= 3
    assert cast(int, stages["reasoning"]["actions"]) >= 3
    assert stages["sandbox_outcome"]["terminal_state"] == "COMPLETED"
    assert stages["revision_memory_receipt"]["semantic_truth_certified"] is False
    assert result.manifest["external_write_performed"] is False
    generators = [call for call in model.calls if call.role == ModelRole.HYPOTHESIS_GENERATOR]
    assert generators and all(
        call.prompt_version == "hypothesis_portfolio.v3.3" for call in generators
    )
    assert any(call.role == ModelRole.HYPOTHESIS_TABLE_FILLER for call in model.calls)
    portfolios = [item for item in model.outputs if isinstance(item, HypothesisPortfolioV3)]
    fills = [item for item in model.outputs if isinstance(item, HypothesisTableFill)]
    assert portfolios and fills
    assert all(len(item.hypotheses) == 3 for item in portfolios)
    assert all(
        len(test.expected_by_hypothesis) == 1
        for item in portfolios
        for hypothesis in item.hypotheses
        for test in hypothesis.discriminating_tests
    )
    assert all(len(test.rows) == 2 for item in fills for test in item.tests)
