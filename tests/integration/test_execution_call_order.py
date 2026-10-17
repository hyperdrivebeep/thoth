"""Execution call-order golden (93): ids, clock, storage reads and writes, record digests.

One request per scenario is traced on a real runtime with deterministic ids and clock. The golden
file is generated from the pre-move ExecutionService (THOTH_WRITE_GOLDEN=1), reviewed and frozen;
the move of pure record construction out of the service must leave it byte-equal. Reads of
the plan, attempts, executions, reconciliations, authorizations and evidence are part of the trace.
"""

from __future__ import annotations

import gc
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration import test_m3_execution_effect_atomicity as effect
from tests.integration import test_m3_execution_revision_atomicity as revision
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxReceipt,
    SandboxResult,
    SandboxRuntimeProfile,
)

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "execution_call_order_golden.json"
BASE = datetime(2026, 10, 15, tzinfo=UTC)
RPC = {
    "start": "rev",
    "pause": "rev",
    "resume": "rev",
    "cancel": "rev",
    "retry": "rev",
    "reconcile_na": "eff",
    "reconcile_ap": "eff",
    "reconcile_partial": "eff",
    "reconcile_unknown": "eff",
    "reconcile_bogus": "eff",
    "link": "eff",
    "compensation": "eff",
    "invalidate": "eff",
    "clear": "eff",
}
SANDBOX = {
    "sandbox_succeeded": (SandboxExecutionState.SUCCEEDED, True),
    "sandbox_failed": (SandboxExecutionState.FAILED, True),
    "sandbox_cancelled": (SandboxExecutionState.CANCELLED, True),
    "sandbox_not_admitted": (SandboxExecutionState.SUCCEEDED, False),
}
METHODS = {
    "reconcile_partial": "TARGET_CHECK_PARTIAL",
    "reconcile_unknown": "TARGET_CHECK_STILL_UNKNOWN",
    "reconcile_bogus": "TARGET_CHECK_NOT_A_METHOD",
}


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
        for field in ("revision_digest", "effect_digest", "reconciliation_digest", "event_digest"):
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
                rec.note(short, name, *[a for a in args if isinstance(a, str)][1:])
            return original(self, *args, **kwargs)

        monkeypatch.setattr(owner, name, traced)

    for name in ("insert_snapshot", "insert_revision", "insert_receipt"):
        wrap(SqliteLedgerTransaction, "L", name, "write")
    wrap(SqliteLedgerTransaction, "L", "set_head", "head")
    for name in (
        "add_attempt",
        "add_effect",
        "add_reconciliation",
        "add_execution",
        "append_audit",
    ):
        wrap(SqliteExecutionStore, "E", name, "write")
    for name in ("add_action", "append_audit"):
        wrap(SqliteActionStore, "A", name, "write")
    for name in ("list_attempts", "read_attempt", "read_execution", "read_reconciliation"):
        wrap(SqliteExecutionStore, "E", name, "read")
    for name in ("read_plan", "list_authorizations"):
        wrap(SqliteActionStore, "A", name, "read")
    wrap(ScopedArtifactLedger, "S", "read_evidence", "read")


async def run_rpc(path: Path, name: str, rec: Recorder) -> list[list[Any]]:
    module = revision if RPC[name] == "rev" else effect
    kind = "reconcile_na" if name in METHODS else name
    runtime, _ctx, method, prepared = await module.prepare(path, kind)
    payload: dict[str, Any] = prepared
    try:
        if name in METHODS:
            payload = {**payload, "reconciliation_method": METHODS[name]}
        command = request(method, "93-golden-" + name, payload)
        rec.log.clear()
        rec.on = True
        try:
            await runtime.bus.dispatch(command)
        finally:
            rec.on = False
        return list(rec.log)
    finally:
        runtime.close()


async def run_sandbox(path: Path, name: str, rec: Recorder) -> list[list[Any]]:
    state, admitted = SANDBOX[name]
    runtime, ctx = await revision.begin(path)
    try:
        started = await revision.call(
            runtime, "execution/start", "93-golden-start", revision.start_input(runtime, ctx)
        )
        attempt = started["attempts"][0]
        (service,) = [
            item
            for item in gc.get_objects()
            if isinstance(item, ExecutionService)
            and getattr(item, "_ledger", None) is runtime.ledger
        ]
        result = SandboxResult(
            project_id=ctx["project"],
            attempt_id=attempt["attempt_id"],
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            state=state,
            started_at=BASE,
            completed_at=BASE,
            cleanup_state=SandboxExecutionState.DESTROYED,
        )
        receipt = SandboxReceipt(
            project_id=ctx["project"],
            attempt_id=attempt["attempt_id"],
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            image_digest="scripted",
            policy_digest="a" * 64,
            input_digests=(),
            output_digests=(),
            result_state=state,
            cleanup_state=SandboxExecutionState.DESTROYED,
            recorded_at=BASE,
            receipt_digest="b" * 64,
        )
        rec.log.clear()
        rec.on = True
        try:
            service.complete_sandbox_attempt(
                PlanExecutionRecord.model_validate(started["execution"]),
                ActionPlanRecord.model_validate(ctx["plan"]),
                StepExecutionAttemptRecord.model_validate(attempt),
                result=result,
                receipt=receipt,
                observation_refs=tuple(ctx["spans"]) if not admitted else (),
                observation_admitted=admitted,
            )
        finally:
            rec.on = False
        return list(rec.log)
    finally:
        runtime.close()


def load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}


@pytest.mark.parametrize("name", [*RPC, *SANDBOX])
async def test_execution_call_order_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    trace = await (run_rpc if name in RPC else run_sandbox)(tmp_path, name, rec)
    assert trace, "the traced request made no recorded call"
    if os.environ.get("THOTH_WRITE_GOLDEN") == "1":
        # Written only from the pre-move source; the output path is named in the 93 report.
        target = Path(os.environ.get("THOTH_GOLDEN_OUT", str(GOLDEN)))
        data: dict[str, Any] = (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        )
        data[name] = trace
        target.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert trace == load_golden()[name]
