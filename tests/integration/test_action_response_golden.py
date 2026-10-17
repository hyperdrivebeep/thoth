"""Action response golden (91): response bytes and port-call order of every mutation route.

One request per scenario runs on a real runtime with deterministic ids and clock. The golden file
is generated from the pre-move ActionHandlers (THOTH_WRITE_GOLDEN=1), reviewed and frozen. Moving
the pure response assembly out of the handlers must leave the full JSON-RPC response (key order
included) and the id, clock, storage-read and storage-write sequence unchanged.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration import test_m3_action_authorization_atomicity as authorization
from tests.integration import test_m3_action_mutation_atomicity as mutation
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "action_response_golden.json"
BASE = datetime(2026, 10, 15, tzinfo=UTC)
MUTATIONS = [
    "create",
    "portfolio_compose",
    "evaluate",
    "recommend",
    "select",
    "plan_compose",
    "plan_revalidate",
    "step_add",
    "step_add_missing_id",
    "step_revise",
    "impact",
    "policy",
    "compensation",
    "merge",
]
AUTHORIZATIONS = {"authorization_prepare": "prepare", "authorization_decide": "decide"}


class Recorder:
    def __init__(self) -> None:
        self.on = False
        self.log: list[list[Any]] = []
        self.ids = 0
        self.ticks = 0

    def note(self, *entry: Any) -> None:
        if self.on:
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
        for field in ("revision_digest", "event_digest"):
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
    for name in ("add_action", "add_portfolio", "add_plan", "add_authorization", "append_audit"):
        wrap(SqliteActionStore, "A", name, "write")
    for name in (
        "list_actions",
        "read_action",
        "list_portfolios",
        "read_portfolio",
        "list_plans",
        "read_plan",
        "read_authorization",
        "list_authorizations",
        "list_audit",
    ):
        wrap(SqliteActionStore, "A", name, "read")
    for name in ("list_objects", "read_object"):
        wrap(SqliteDecisionObjectStore, "O", name, "read")


async def run(path: Path, name: str, rec: Recorder) -> dict[str, Any]:
    if name in AUTHORIZATIONS:
        runtime, _ctx, command_parts = await authorization.prepare(path, AUTHORIZATIONS[name])
        method, payload = command_parts
    else:
        kind = "step_add" if name == "step_add_missing_id" else name
        runtime, ctx = await mutation.prepare(path, kind)
        method, prepared, _event, _key = mutation.command_input(ctx, kind)
        payload: dict[str, Any] = prepared
        if name == "step_add_missing_id":
            payload = {
                **payload,
                "step_spec": {k: v for k, v in payload["step_spec"].items() if k != "step_id"},
            }
    try:
        command = request(method, "91-golden-" + name, payload)
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


@pytest.mark.parametrize("name", [*MUTATIONS, *AUTHORIZATIONS])
async def test_action_response_and_call_order_are_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    observed = await run(tmp_path, name, rec)
    assert observed["trace"], "the traced request made no recorded call"
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
