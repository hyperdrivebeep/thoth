from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, event
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import prepare_project, request, value


@pytest.mark.parametrize(
    "method,record_type",
    [("closure/retention/plan", "RETENTION"), ("closure/purge/prepare", "PURGE_ACTION_CANDIDATE")],
)
async def test_single_record_preparation_is_atomic_without_new_outer_uow(
    tmp_path: Path, method: str, record_type: str
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        readiness = value(
            await runtime.bus.dispatch(
                request(
                    "closure/readiness/assess",
                    "readiness",
                    {
                        "project_id": project,
                        "closure_scope": "PROJECT_PHASE",
                        "scope_ref": project,
                        "disposition": "DEFERRED",
                        "open_items": [],
                        "required_receipt_refs": [],
                    },
                )
            )
        )["readiness"]
        closure = value(
            await runtime.bus.dispatch(
                request(
                    "closure/decide",
                    "decide",
                    {
                        "project_id": project,
                        "readiness_id": readiness["record_id"],
                        "decision": "DECIDE",
                        "actor_ref": "human:local-user",
                        "reason": "Record the scoped deferred disposition",
                    },
                )
            )
        )["closure"]
        extra = (
            {"retention_policy_ref": "retention:test", "legal_or_security_hold": True}
            if record_type == "RETENTION"
            else {"exact_scope_refs": [project], "reason": "Prepare only"}
        )
        command = request(
            method, "fault", {"project_id": project, "closure_id": closure["record_id"], **extra}
        )
        before = snapshot(runtime.ledger.engine)
        reached = False

        def after_sql(
            connection: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            nonlocal reached
            if not statement.lstrip().upper().startswith("INSERT INTO CONTROL_RECORDS"):
                return
            compiled: list[dict[str, Any]] = context.compiled_parameters or []
            if any(p.get("record_type") == record_type for p in compiled):
                assert connection.in_transaction()
                reached = True
                raise RuntimeError("inside single-record SQL transaction")

        event.listen(runtime.ledger.engine, "after_cursor_execute", after_sql)
        try:
            failed = await runtime.bus.dispatch(command)
        finally:
            event.remove(runtime.ledger.engine, "after_cursor_execute", after_sql)
        assert reached and failed.error is not None
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert_phase_delta(before, snapshot(runtime.ledger.engine), allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        result = value(
            await reopened.bus.dispatch(request(method, "retry", dict(command.params.input)))
        )
        if record_type == "RETENTION":
            assert result["retention"]["state"] == "ON_HOLD"
            assert result["retention"]["payload"]["archive_implies_delete"] is False
        else:
            assert result["deletion_performed"] is False
            assert result["purge_action_candidate"]["state"] == "HUMAN_REQUIRED_R3"
    finally:
        reopened.close()
