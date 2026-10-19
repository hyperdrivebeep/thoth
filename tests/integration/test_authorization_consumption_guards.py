"""Stale and concurrent consumers receive a typed rejection without another write."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock
from typing import Any, cast

import pytest
from tests.integration import storage_coverage_helpers
from tests.integration.storage_coverage_helpers import domain_snapshot
from tests.integration.test_a04_storage_authority import prepare_r3, start_request
from tests.integration.test_approval_clock_rollback import (
    APPROVED_AT,
    InjectedClock,
    NoModels,
    history,
)
from tests.integration.test_approval_clock_rollback import (
    isolated_metadata_only as isolated_metadata_only,
)

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.action import SqliteActionStore
from thoth.application.services.action_service import ActionService
from thoth.apps.runtime import AppRuntime
from thoth.domain.action_full import AuthorizationEnvelopeRecord
from thoth.domain.authorization_errors import AuthorizationAlreadyConsumed
from thoth.protocol.jsonrpc import RpcErrorCode


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    def now(_clock: SystemClock) -> datetime:
        return APPROVED_AT

    monkeypatch.setattr(SystemClock, "now", now)


def action_service(runtime: AppRuntime) -> ActionService:
    # Test-only access to the actual service wired to the ordinary RPC handler.
    handler = cast(Any, runtime.bus)._registry.resolve("execution/start")
    return cast(ActionService, handler.__self__._service._action_service)


@pytest.mark.parametrize("stale_current_read", (False, True))
async def test_stale_expected_and_successor_guard_leave_no_new_domain_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_metadata_only: NoModels,
    stale_current_read: bool,
) -> None:
    runtime, project, plan, grant = await prepare_r3(tmp_path / "workspace")
    store = SqliteActionStore(runtime.ledger.engine)
    expected = store.read_authorization(project, str(grant["authorization_id"]))
    assert expected is not None
    first = await runtime.bus.dispatch(start_request(runtime, project, plan, "first"))
    assert first.error is None
    before = domain_snapshot(runtime.ledger.engine)
    checked_successor: list[bool] = []
    read = SqliteActionStore.read_authorization
    has_successor = SqliteActionStore.has_authorization_successor

    def stale_read(
        self: SqliteActionStore, project_id: str, authorization_id: str
    ) -> AuthorizationEnvelopeRecord | None:
        if (
            stale_current_read
            and project_id == project
            and authorization_id == expected.authorization_id
        ):
            return expected
        return read(self, project_id, authorization_id)

    def observe_successor(
        self: SqliteActionStore, project_id: str, authorization_id: str, digest: str
    ) -> bool:
        found = has_successor(self, project_id, authorization_id, digest)
        checked_successor.append(found)
        return found

    monkeypatch.setattr(SqliteActionStore, "read_authorization", stale_read)
    monkeypatch.setattr(SqliteActionStore, "has_authorization_successor", observe_successor)
    with pytest.raises(AuthorizationAlreadyConsumed) as caught:
        action_service(runtime).consume_authorization(
            expected, exact_scope_digest=expected.exact_scope_digest
        )
    assert caught.value.reason_code == "AUTHORIZATION_ALREADY_CONSUMED"
    assert checked_successor == ([True] if stale_current_read else [])
    assert domain_snapshot(runtime.ledger.engine) == before
    assert isolated_metadata_only.calls == 0


async def test_stale_selection_is_a_typed_rpc_rejection_not_an_audit_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_metadata_only: NoModels,
) -> None:
    runtime, project, plan, grant = await prepare_r3(tmp_path / "workspace")
    expected = SqliteActionStore(runtime.ledger.engine).read_authorization(
        project, str(grant["authorization_id"])
    )
    assert expected is not None
    first = await runtime.bus.dispatch(start_request(runtime, project, plan, "first"))
    assert first.error is None
    before = domain_snapshot(runtime.ledger.engine)
    original = SqliteActionStore.list_authorizations

    def stale_selection(
        self: SqliteActionStore, project_id: str, plan_id: str | None
    ) -> tuple[AuthorizationEnvelopeRecord, ...]:
        if project_id == project and plan_id == plan["plan_id"]:
            return (expected,)
        return original(self, project_id, plan_id)

    monkeypatch.setattr(SqliteActionStore, "list_authorizations", stale_selection)
    response = await runtime.bus.dispatch(start_request(runtime, project, plan, "stale"))
    assert response.error is not None and response.error.code == RpcErrorCode.DOMAIN_REJECTED
    assert response.error.data == {"reason_code": "AUTHORIZATION_ALREADY_CONSUMED"}
    assert domain_snapshot(runtime.ledger.engine) == before
    assert (
        len(
            [
                row
                for row in history(runtime, project, expected.authorization_id)
                if row.state == "CONSUMED"
            ]
        )
        == 1
    )
    assert isolated_metadata_only.calls == 0


async def test_two_real_consumers_have_one_winner_and_one_typed_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_metadata_only: NoModels,
) -> None:
    workspace = tmp_path / "workspace"
    first, project, _plan, grant = await prepare_r3(workspace)
    second = storage_coverage_helpers.create_runtime(workspace)
    expected = SqliteActionStore(first.ledger.engine).read_authorization(
        project, str(grant["authorization_id"])
    )
    assert expected is not None
    barrier = Barrier(2)
    clock_lock = Lock()
    clock = InjectedClock(APPROVED_AT - timedelta(minutes=1), timedelta(milliseconds=1))

    def advancing_rollback(_clock: SystemClock) -> datetime:
        with clock_lock:
            return clock.now()

    monkeypatch.setattr(SystemClock, "now", advancing_rollback)

    def consume(runtime: AppRuntime) -> tuple[str, dict[str, tuple[str, ...]]]:
        barrier.wait(timeout=10)
        try:
            result = action_service(runtime).consume_authorization(
                expected, exact_scope_digest=expected.exact_scope_digest
            )
            assert result.state == "CONSUMED"
            state = "CONSUMED"
        except AuthorizationAlreadyConsumed as exc:
            state = exc.reason_code
        return state, domain_snapshot(runtime.ledger.engine)

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(consume, runtime) for runtime in (first, second)]
        outcomes = [job.result(timeout=20) for job in jobs]
    assert sorted(state for state, _snapshot in outcomes) == [
        "AUTHORIZATION_ALREADY_CONSUMED",
        "CONSUMED",
    ]
    assert outcomes[0][1] == outcomes[1][1] == domain_snapshot(first.ledger.engine)
    consumed = [
        row for row in history(first, project, expected.authorization_id) if row.state == "CONSUMED"
    ]
    assert len(consumed) == 1 and consumed[0].supersedes_revision_digest == expected.revision_digest
    assert consumed[0].created_at < APPROVED_AT and clock.current < APPROVED_AT
    assert isolated_metadata_only.calls == 0
