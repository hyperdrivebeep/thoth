"""Observe every clock call across the public RPC scenario, including setup/close."""

import json
from pathlib import Path

import pytest
from tests.integration.counter_clock_observation import observe


@pytest.mark.parametrize("scenario", ["converged", "budget"])
async def test_normal_entry_clock_sequence(
    scenario: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = json.loads(Path(__file__).with_name("counter_clock_sequence.json").read_text())
    assert await observe(scenario, tmp_path, monkeypatch) == expected[scenario]
