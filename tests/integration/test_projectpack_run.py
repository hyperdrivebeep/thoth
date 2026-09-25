from __future__ import annotations

from pathlib import Path

import pytest

from thoth.adapters.projectpacks import load_project_pack
from thoth.apps.projectpack_execution import run_project_pack
from thoth.domain.enums import ExecutionAuthority, SufficiencyStatus


@pytest.mark.asyncio
async def test_example_projectpack_runs_without_core_scenario_hardcoding(tmp_path: Path) -> None:
    pack_root = Path(__file__).resolve().parents[2] / "examples" / "projectpacks" / "demo-system"
    pack = load_project_pack(pack_root, include_scripted=True)

    result = await run_project_pack(pack, workspace=tmp_path / "workspace")

    assert result.pack_id == "pack:demo-system"
    assert result.evidence_count == 6
    assert len(result.cycle.portfolio.hypotheses) == 3
    assert len({item.action_family for item in result.cycle.action_plan.alternatives}) == 3
    assert SufficiencyStatus.EXPERT_INPUT_REQUIRED in result.cycle.assessment.derived_status
    protected = next(
        item
        for item in result.cycle.action_plan.alternatives
        if item.execution_authority == ExecutionAuthority.HUMAN_REQUIRED_R3
    )
    assert protected.state.value == "APPROVAL_PENDING"
    assert result.cycle.commit.receipt.semantic_truth_certified is False
    assert result.scripted_model is True
