"""D publication: freeze successful RPC values and ID/clock order before changing UoW scope."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_m5_revision_control_atomicity import harness
from tests.integration.test_revision_merge_late_write_observation import prepare_siblings
from tests.integration.test_revision_remaining_publication_observations import prepare

from thoth.adapters.runtime import SystemClock, UuidIdGenerator

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/revision_publication_success_golden.json"


@pytest.mark.parametrize("kind", ["o1", "o2", "o3"])
async def test_publication_success_values_and_clock_id_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    trace: list[list[object]] = []
    ticks = 0
    ids = 0

    def now(_self: Any) -> datetime:
        nonlocal ticks
        ticks += 1
        trace.append(["clock", ticks])
        return datetime(2026, 10, 15, tzinfo=UTC) + timedelta(milliseconds=ticks)

    def new(_self: Any, prefix: str) -> str:
        nonlocal ids
        ids += 1
        trace.append(["id", prefix, ids])
        return f"{prefix}:{ids:08d}"

    monkeypatch.setattr(SystemClock, "now", now)
    monkeypatch.setattr(UuidIdGenerator, "new", new)
    async with harness(tmp_path) as h:
        if kind == "o1":
            body = await prepare_siblings(h)
            method = "revision/merge/propose"
        else:
            scenario = await prepare(h, kind)
            body, method = scenario.body, scenario.method
        start = len(trace)
        result = value(await h.call(method, "d-success", body))
        assert h.model.calls == 0
        observed = {"value": result, "id_clock_trace": trace[start:]}
    target = os.environ.get("THOTH_D_GOLDEN_OUT")
    if target:
        path = Path(target)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[kind] = observed
        path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    else:
        assert observed == json.loads(GOLDEN.read_text(encoding="utf-8"))[kind]
