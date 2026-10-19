"""F-03 regression: one approval stays consumed even when the clock moves backwards.

Only SystemClock is changed, never the system clock. Existing R3 helpers use the
local-metadata-only profile; model resolution, network sends and processes are forbidden.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, cast

import httpx
import pytest
from sqlalchemy import event, func, literal_column, select
from sqlalchemy.engine import ExceptionContext
from tests.integration import storage_coverage_helpers
from tests.integration.test_a04_storage_authority import prepare_r3, start_request

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.schema import (
    action_authorizations,
    execution_effects,
    step_execution_attempts,
)
from thoth.apps.runtime import AppRuntime
from thoth.domain.action_full import AuthorizationEnvelopeRecord
from thoth.domain.execution_full import StepExecutionAttemptRecord
from thoth.ports.model import ModelPort

APPROVED_AT = datetime(2026, 10, 19, 12, tzinfo=UTC)


class NoModels:
    calls = 0

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        del provider, model
        self.calls += 1
        raise AssertionError("model resolution is forbidden in the clock baseline")


@pytest.fixture
def isolated_metadata_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoModels]:
    models = NoModels()
    runtimes: list[AppRuntime] = []
    original = storage_coverage_helpers.create_runtime
    forbidden_calls: list[str] = []

    def runtime(workspace: Path) -> AppRuntime:
        # Explicit resolver avoids automatic account-catalog refresh during setup.
        result = original(workspace, model_resolver=models)
        runtimes.append(result)
        return result

    def forbidden_process(*args: object, **kwargs: object) -> NoReturn:
        forbidden_calls.append("process")
        raise AssertionError("process creation is forbidden in the clock baseline")

    def forbidden_network(*args: object, **kwargs: object) -> NoReturn:
        forbidden_calls.append("network")
        raise AssertionError("network send is forbidden in the clock baseline")

    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "synthetic-home" / name.lower()))
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(storage_coverage_helpers, "create_runtime", runtime)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    monkeypatch.setattr(httpx.Client, "send", forbidden_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_network)
    try:
        yield models
    finally:
        for created in reversed(runtimes):
            created.close()
        assert models.calls == 0
        assert forbidden_calls == []


@dataclass
class InjectedClock:
    current: datetime = APPROVED_AT
    step: timedelta = timedelta(0)

    def now(self) -> datetime:
        result = self.current
        self.current += self.step
        return result


def history(
    runtime: AppRuntime, project: str, authorization: str
) -> list[AuthorizationEnvelopeRecord]:
    with runtime.ledger.engine.connect() as connection:
        rows = connection.execute(
            select(action_authorizations.c.content_json)
            .where(
                action_authorizations.c.project_id == project,
                action_authorizations.c.authorization_id == authorization,
            )
            .order_by(literal_column("rowid"))
        ).scalars()
        return [AuthorizationEnvelopeRecord.model_validate_json(str(row)) for row in rows]


def attempts(runtime: AppRuntime, project: str) -> list[StepExecutionAttemptRecord]:
    with runtime.ledger.engine.connect() as connection:
        rows = connection.execute(
            select(step_execution_attempts.c.content_json).where(
                step_execution_attempts.c.project_id == project
            )
        ).scalars()
        return [StepExecutionAttemptRecord.model_validate_json(str(row)) for row in rows]


def diagnostics(workspace: Path) -> dict[str, object]:
    path = workspace / "diagnostics" / "internal-failures.jsonl"
    types: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            record: object = json.loads(line)
            if isinstance(record, dict):
                kind = cast(dict[str, object], record).get("exception_type")
                if isinstance(kind, str):
                    types.append(kind)
    return {"exists": path.exists(), "exception_types": types}


# The normal response contract also applies to both rollback clocks.
@pytest.mark.parametrize("scenario", ("normal", "rollback-fixed", "rollback-advancing"))
async def test_current_approval_clock_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_metadata_only: NoModels,
    scenario: str,
) -> None:
    clock = InjectedClock()

    def injected_now(_clock: SystemClock) -> datetime:
        return clock.now()

    monkeypatch.setattr(SystemClock, "now", injected_now)
    workspace = tmp_path / "workspace"
    runtime, project, plan, approval = await prepare_r3(workspace)
    store = SqliteActionStore(runtime.ledger.engine)
    original = store.read_authorization(project, str(approval["authorization_id"]))
    assert original is not None and original.state == "APPROVED" and original.single_use
    assert original.created_at == APPROVED_AT
    assert not [
        row
        for row in history(runtime, project, original.authorization_id)
        if row.state == "CONSUMED"
    ]
    sql_errors: list[dict[str, object]] = []

    def observe_error(context: ExceptionContext) -> None:
        wrapper = context.sqlalchemy_exception
        original_error = context.original_exception
        # Do not retain SQL parameters, raw exception text, paths or provider output.
        sql_errors.append(
            {
                "sqlalchemy_type": None if wrapper is None else type(wrapper).__name__,
                "dbapi_type": type(original_error).__name__,
                "action_audit_insert": (context.statement or "").startswith(
                    "INSERT INTO action_audit"
                ),
                "audit_digest_unique": str(original_error)
                == "UNIQUE constraint failed: action_audit.event_digest",
            }
        )

    event.listen(runtime.ledger.engine, "handle_error", observe_error)
    try:
        clock.current = (
            APPROVED_AT + timedelta(seconds=1)
            if scenario == "normal"
            else APPROVED_AT - timedelta(minutes=1)
        )
        clock.step = timedelta(0) if scenario == "rollback-fixed" else timedelta(milliseconds=1)
        first = await runtime.bus.dispatch(start_request(runtime, project, plan, "clock-first"))
        assert first.error is None and first.result is not None
        current_first = store.read_authorization(project, original.authorization_id)
        assert current_first is not None
        first_consumed = [
            row
            for row in history(runtime, project, original.authorization_id)
            if row.state == "CONSUMED"
        ]
        assert len(first_consumed) == 1
        assert len(attempts(runtime, project)) == 1
        second = await runtime.bus.dispatch(start_request(runtime, project, plan, "clock-second"))
        current_second = store.read_authorization(project, original.authorization_id)
        assert current_second is not None
        rows = history(runtime, project, original.authorization_id)
        consumed = [row for row in rows if row.state == "CONSUMED"]
        persisted_attempts = attempts(runtime, project)
        listed = store.list_authorizations(project, str(plan["plan_id"]))
        with runtime.ledger.engine.connect() as connection:
            effect_rows = int(
                connection.execute(
                    select(func.count())
                    .select_from(execution_effects)
                    .where(execution_effects.c.project_id == project)
                ).scalar_one()
            )
        report: dict[str, object] = {
            "scenario": scenario,
            "first_code": None if first.error is None else int(first.error.code),
            "second_code": None if second.error is None else int(second.error.code),
            "current_after_first": current_first.state,
            "current_after_second": current_second.state,
            "current_still_original_approval": current_second.revision_digest
            == original.revision_digest,
            "listed_states": [row.state for row in listed],
            "stored_states": [row.state for row in rows],
            "consumed_rows": len(consumed),
            "attempt_rows": len(persisted_attempts),
            "effect_rows": effect_rows,
            "consumed_times": [row.created_at.isoformat() for row in consumed],
            "all_consumed_from_original": all(
                row.supersedes_revision_digest == original.revision_digest for row in consumed
            ),
            "diagnostics": diagnostics(workspace),
            "sqlalchemy_observation": sql_errors,
            "model_calls": isolated_metadata_only.calls,
            "profile": "local-metadata-only",
        }
        print("VF03 " + json.dumps(report, sort_keys=True))
        assert all(
            row.single_use and row.exact_scope_digest == original.exact_scope_digest
            for row in consumed
        )
        # The existing attempt contract binds exact scope, not an authorization revision.
        assert {row.authorization_digest for row in persisted_attempts} == {
            original.exact_scope_digest
        }
        assert all(row.effect_state == "POSSIBLE" for row in persisted_attempts)
        assert effect_rows == 0
        if scenario == "normal":
            assert current_first.state == current_second.state == "CONSUMED"
            assert len(consumed) == len(persisted_attempts) == 1
            assert second.error is None and second.result is not None
            second_value = second.result["value"]
            assert isinstance(second_value, dict)
            execution = second_value["execution"]
            assert isinstance(execution, dict)
            assert execution["protected_steps"] == ["step:r3"]
            assert execution["executable_steps"] == [] and execution["attempt_refs"] == []
            assert sql_errors == []
        else:
            assert current_first.state == current_second.state == "CONSUMED"
            assert current_second.revision_digest == consumed[0].revision_digest
            assert current_second.revision_digest != original.revision_digest
            assert [row.state for row in listed] == ["CONSUMED"]
            assert clock.current < APPROVED_AT
            assert len(consumed) == len(persisted_attempts) == 1
            assert consumed[0].created_at < APPROVED_AT
            assert consumed[0].supersedes_revision_digest == original.revision_digest
            assert second.error is None and second.result is not None
            second_value = second.result["value"]
            assert isinstance(second_value, dict)
            execution = second_value["execution"]
            assert isinstance(execution, dict)
            assert execution["protected_steps"] == ["step:r3"]
            assert execution["executable_steps"] == [] and execution["attempt_refs"] == []
            assert sql_errors == []
    finally:
        event.remove(runtime.ledger.engine, "handle_error", observe_error)
