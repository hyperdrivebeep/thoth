"""Outcome response golden (95): response bytes and port-call order of every Outcome mutation route.

One request per scenario runs on a real runtime with deterministic ids and clock. The golden file
is generated from the pre-move OutcomeService (THOTH_WRITE_GOLDEN=1), reviewed and frozen. Moving
the pure record construction out of the service must leave the full JSON-RPC response (key order
included), the id and clock sequence, every storage read and write with its record digest and the
guard errors unchanged. The built-in profile catalog is traced at runtime start.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration import test_m3_outcome_child_atomicity as child
from tests.integration import test_m3_outcome_revision_atomicity as revision
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.adapters.storage.sqlite import SqliteLedger, SqliteLedgerTransaction
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "outcome_response_golden.json"
BASE = datetime(2026, 10, 15, tzinfo=UTC)
REVISION_KINDS = ["series_create", "observation_link", "assess", "reassess"]
CHILD_KINDS = ["attribution", "change_set", "followup", "impact"]
GUARDS = ["series_create_bad_phase", "observation_link_unknown_phase", "assess_unknown_phase"]
SEED = "seed_profiles"


class Recorder:
    def __init__(self) -> None:
        self.on = False
        self.only_profiles = False
        self.log: list[list[Any]] = []
        self.ids = 0
        self.ticks = 0

    def note(self, *entry: Any) -> None:
        if not self.on:
            return
        if self.only_profiles and entry[:2] != ("O", "put_profile"):
            return
        self.log.append(list(entry))


def install(monkeypatch: pytest.MonkeyPatch, rec: Recorder) -> None:
    def new(self: Any, prefix: str) -> str:
        normalized = prefix.strip().lower().replace("_", "-")
        rec.ids += 1
        rec.note("ids", normalized)
        return f"{normalized}:{rec.ids:08d}"

    def now(self: Any) -> datetime:
        rec.ticks += 1
        rec.note("clock")
        return BASE + timedelta(milliseconds=rec.ticks)

    monkeypatch.setattr(UuidIdGenerator, "new", new)
    monkeypatch.setattr(SystemClock, "now", now)

    def digest_of(record: Any) -> list[Any]:
        for field in (
            "revision_digest",
            "attribution_digest",
            "change_set_digest",
            "impact_digest",
            "profile_digest",
            "event_digest",
        ):
            if hasattr(record, field):
                return [field, getattr(record, field)]
        return []

    def wrap(owner: Any, short: str, name: str, mode: str) -> None:
        original = getattr(owner, name)

        def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
            if mode == "write":
                record = args[0]
                rec.note(short, name, getattr(record, "event_type", None), *digest_of(record))
            elif mode == "head":
                rec.note(short, name, args[1], args[2])
            else:
                rec.note(short, name, *[a for a in args if isinstance(a, str)])
            return original(self, *args, **kwargs)

        monkeypatch.setattr(owner, name, traced)

    for name in ("insert_snapshot", "insert_revision", "insert_receipt"):
        wrap(SqliteLedgerTransaction, "L", name, "write")
    wrap(SqliteLedgerTransaction, "L", "set_head", "head")
    for name in ("read_revision_by_digest", "read_snapshot"):
        wrap(SqliteLedger, "L", name, "read")
    for name in (
        "put_profile",
        "add_series",
        "add_assessment",
        "add_attribution",
        "add_change_set",
        "add_impact",
        "append_audit",
    ):
        wrap(SqliteOutcomeStore, "O", name, "write")
    for name in (
        "list_profiles",
        "read_profile",
        "list_series",
        "read_series",
        "list_assessments",
        "read_assessment",
        "read_attribution",
        "read_change_set",
        "read_impact",
        "list_audit",
    ):
        wrap(SqliteOutcomeStore, "O", name, "read")
    wrap(SqliteDecisionObjectStore, "D", "read_object", "read")
    wrap(SqliteActionStore, "A", "list_plans", "read")
    wrap(SqliteExecutionStore, "E", "read_execution", "read")
    wrap(ScopedArtifactLedger, "S", "read_evidence", "read")
    wrap(BaselineService, "B", "list_sets", "read")
    wrap(BaselineService, "B", "require_current_comparator", "read")


async def run(path: Path, name: str, rec: Recorder) -> dict[str, Any]:
    if name == SEED:
        rec.only_profiles = True
        rec.log.clear()
        rec.on = True
        try:
            runtime = create_runtime(path)
        finally:
            rec.on = False
            rec.only_profiles = False
        runtime.close()
        return {"trace": list(rec.log), "response_text": ""}
    kind = {
        "series_create_bad_phase": "series_create",
        "observation_link_unknown_phase": "observation_link",
        "assess_unknown_phase": "assess",
    }.get(name, name)
    module = child if kind in CHILD_KINDS else revision
    runtime, _ctx, method, prepared = await module.prepare(path, kind)
    payload: dict[str, Any] = prepared
    if name == "series_create_bad_phase":
        payload = {
            **payload,
            "assessment_windows": [{"assessment_phase": "NOT_A_PHASE", "window": "bad"}],
        }
    elif name in {"observation_link_unknown_phase", "assess_unknown_phase"}:
        payload = {**payload, "assessment_phase": "FINAL_WITHIN_SCOPE"}
    try:
        command = request(method, "95-golden-" + name, payload)
        rec.log.clear()
        rec.on = True
        try:
            response = await runtime.bus.dispatch(command)
        finally:
            rec.on = False
        return {
            "trace": list(rec.log),
            "response_text": json.dumps(response.model_dump(mode="json"), ensure_ascii=False),
        }
    finally:
        runtime.close()


def load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}


@pytest.mark.parametrize("name", [*REVISION_KINDS, *CHILD_KINDS, *GUARDS, SEED])
async def test_outcome_response_and_call_order_are_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    observed = await run(tmp_path, name, rec)
    assert observed["trace"], "the traced step made no recorded call"
    if os.environ.get("THOTH_WRITE_GOLDEN") == "1":
        target = Path(os.environ.get("THOTH_GOLDEN_OUT", str(GOLDEN)))
        data: dict[str, Any] = (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        )
        data[name] = observed
        target.write_text(
            json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        return
    expected = load_golden()[name]
    assert observed["trace"] == expected["trace"]
    assert observed["response_text"] == expected["response_text"]
