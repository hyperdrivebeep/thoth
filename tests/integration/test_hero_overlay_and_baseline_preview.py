from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel


@pytest.mark.asyncio
async def test_hero_preserves_overlay_cutoff_and_protected_preview_boundaries(
    tmp_path: Path,
) -> None:
    result = await run_current_hero(
        pack_name="6g-sandbox-hero",
        workspace=tmp_path / "hero-preview",
        model=GenericProjectPackModel(),
        execution_mode="PREVIEW",
    )
    assert result.manifest["overlay"] == "eu-rnd-systems-integration"
    stages = cast(dict[str, dict[str, object]], result.manifest["stages"])
    assert cast(int, stages["source_cutoff"]["after_cutoff_excluded"]) >= 1
    preview = stages["closure_baseline_r3_preview"]
    assert preview["baseline_decision_performed"] is False
    assert preview["r3_action_performed"] is False
    assert preview["deletion_performed"] is False
    assert result.manifest["beneficiary_pdf_in_public_bundle"] is False
