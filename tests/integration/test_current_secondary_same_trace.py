from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.hero_6g_current_core import run_current_hero
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel


@pytest.mark.asyncio
async def test_sunrise_secondary_uses_same_binary_prompt_policy_schema_trace(
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
    hero_contracts = cast(dict[str, str], hero.manifest["trace_contracts"])
    secondary_contracts = cast(dict[str, str], secondary.manifest["trace_contracts"])
    for key in ("runner", "prompt", "policy_schema", "response_schema"):
        assert hero_contracts[key] == secondary_contracts[key]
    hero_stages = cast(dict[str, object], hero.manifest["stages"])
    secondary_stages = cast(dict[str, object], secondary.manifest["stages"])
    assert tuple(hero_stages) == tuple(secondary_stages)
    assert secondary.manifest["pack_name"] == "sunrise-secondary"
