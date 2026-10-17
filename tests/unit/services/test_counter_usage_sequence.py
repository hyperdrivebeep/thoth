"""Frozen observations collected before the budget usage extraction."""

import json
from pathlib import Path

import pytest
from tests.unit.services.counter_usage_observation import CASES, observe


@pytest.mark.parametrize("case", CASES)
async def test_complete_counter_execution_sequence(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = json.loads(Path(__file__).with_name("counter_usage_sequence.json").read_text())
    assert await observe(case, monkeypatch) == expected[case]
