from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.hero_6g_current_core import run_current_hero
from qa.scenarios.hidden_holdout_evaluator import evaluate_hidden_holdout
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel


@pytest.mark.asyncio
async def test_opendreamkit_oracle_is_an_independent_post_run_evaluator_input(
    tmp_path: Path,
) -> None:
    result = await run_current_hero(
        pack_name="opendreamkit-hidden-holdout",
        workspace=tmp_path / "holdout",
        model=GenericProjectPackModel(),
        execution_mode="SEALED_REPLAY",
    )
    oracle = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "projectpacks"
        / "opendreamkit-hidden-holdout"
        / "oracle"
        / "expected-invariants.json"
    )
    evaluation = evaluate_hidden_holdout(result.observations, oracle)
    assert evaluation["verdict"] == "PASS"
    checks = cast(list[dict[str, object]], evaluation["checks"])
    assert all(item["state"] == "PASS" for item in checks)
    assert evaluation["oracle_runtime_visible"] is False
    paths = cast(list[str], result.manifest["runtime_source_paths"])
    assert all(not path.casefold().startswith("oracle/") for path in paths)
    assert evaluation["semantic_truth_certified"] is False
