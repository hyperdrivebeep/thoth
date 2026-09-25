from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel


@pytest.mark.asyncio
async def test_current_hero_enters_through_one_normal_thread_input(tmp_path: Path) -> None:
    result = await run_current_hero(
        pack_name="6g-sandbox-hero",
        workspace=tmp_path / "hero",
        model=GenericProjectPackModel(),
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
