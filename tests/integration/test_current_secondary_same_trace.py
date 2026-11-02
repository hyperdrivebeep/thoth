from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel


@pytest.mark.asyncio
async def test_sunrise_secondary_completes_the_same_stage_sequence(
    tmp_path: Path,
) -> None:
    hero = await run_current_hero(
        pack_name="6g-sandbox-hero",
        workspace=tmp_path / "hero",
        model=GenericProjectPackModel(),
        execution_mode="SEALED_REPLAY",
    )
    secondary = await run_current_hero(
        pack_name="sunrise-secondary",
        workspace=tmp_path / "secondary",
        model=GenericProjectPackModel(),
        execution_mode="SEALED_REPLAY",
    )
    hero_stages = cast(dict[str, object], hero.manifest["stages"])
    secondary_stages = cast(dict[str, object], secondary.manifest["stages"])
    assert tuple(hero_stages) == tuple(secondary_stages)
    assert secondary.manifest["pack_name"] == "sunrise-secondary"
