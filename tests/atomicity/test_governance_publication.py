from pathlib import Path

import pytest
from sqlalchemy import Connection
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    prepare_project,
    request,
    value,
)

from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.domain.governance_revision import GovernanceReceipt, GovernanceRevision


@pytest.mark.parametrize(
    "method",
    [
        "project/create",
        "project/metadata/update",
        "project/overlay/update",
        "project/activate",
        "project/policy/update",
        "project/role/assign",
        "project/role/revoke",
        "project/reference/import",
        "project/cutoff/update",
    ],
)
async def test_every_project_publication_rolls_back_receipt_fault_and_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    runtime, project = await prepare_project(tmp_path)
    extra: dict[str, object] = {}
    if method == "project/create":
        project += ":new"
        extra = {"name": "New", "cutoff_at": "2026-09-01T00:00:00Z"}
    elif method == "project/metadata/update":
        extra = {"name": "Renamed"}
    elif method == "project/overlay/update":
        extra = {"overlay": "revised"}
    elif method == "project/policy/update":
        extra = {"payload": {"external_write": False}}
    elif method == "project/role/assign":
        extra = {"actor_id": "human:reviewer", "role": "reviewer"}
    elif method == "project/role/revoke":
        assigned = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "role",
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "actor_id": "human:reviewer",
                        "role": "reviewer",
                    },
                )
            )
        )
        extra = {"role_assignment_id": assigned["role"]["role_assignment_id"]}
    elif method == "project/reference/import":
        extra = {
            "origin_project_id": "project:external",
            "origin_revision": "revision:external",
            "rights_status": "PERMITTED",
            "scope": "PROJECT",
        }
    elif method == "project/cutoff/update":
        cutoff = "2026-09-10T00:00:00Z"
        impact = value(
            await runtime.bus.dispatch(
                request(
                    "project/cutoff/impact",
                    "impact",
                    {"project_id": project, "proposed_cutoff_at": cutoff},
                )
            )
        )
        extra = {"cutoff_at": cutoff, "expected_impact_digest": impact["impact"]["impact_digest"]}
    elif method == "project/activate":
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "source.md").write_text("Controlled source", encoding="utf-8")
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "connect",
                    {
                        "project_id": project,
                        "relative_path": "source.md",
                        "media_type": "text/markdown",
                    },
                )
            )
        )
    current = (
        None
        if method == "project/create"
        else value(
            await runtime.bus.query(request("project/read", "before", {"project_id": project}))
        )
    )
    command = request(
        method,
        "receipt-fault",
        {
            "project_id": project,
            **({} if current is None else {"expected_revision": current["revision"]}),
            **extra,
        },
    )
    before = snapshot(runtime.ledger.engine)
    original = SqliteGovernanceHistory._insert_record  # pyright: ignore[reportPrivateUsage]
    hits: list[str] = []

    def fail(
        connection: Connection, record: GovernanceRevision, receipt: GovernanceReceipt
    ) -> None:
        original(connection, record, receipt)
        hits.append("receipt-written")
        raise RuntimeError("governance receipt failpoint")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteGovernanceHistory, "_insert_record", staticmethod(fail))
            response = await runtime.bus.dispatch(command)
        assert response.error is not None
        if method == "project/policy/update":
            assert hits == ["receipt-written"]
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert_phase_delta(before, snapshot(runtime.ledger.engine), allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        retried = request(method, "retry", dict(command.params.input))
        result = value(await reopened.bus.dispatch(retried))
        history = SqliteGovernanceHistory(reopened.ledger.engine)
        head = history.read_current(project, "PROJECT", project)
        assert head is not None and head.projection["revision"] == result["revision"]
        after = snapshot(reopened.ledger.engine)
        assert value(await reopened.bus.dispatch(retried)) == result
        assert_phase_delta(after, snapshot(reopened.ledger.engine))
    finally:
        reopened.close()


async def test_changed_policy_content_rolls_back_receipt_fault_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project = await prepare_project(tmp_path)
    first = value(
        await runtime.bus.dispatch(
            request(
                "project/policy/update",
                "policy-a",
                {"project_id": project, "expected_revision": 0, "payload": {"label": "A"}},
            )
        )
    )
    payload: dict[str, object] = {
        "project_id": project,
        "expected_revision": 1,
        "payload": {"label": "B"},
    }
    before = domain_snapshot(runtime.ledger.engine)
    original = SqliteGovernanceHistory._insert_record  # pyright: ignore[reportPrivateUsage]
    hits: list[str] = []

    def fail(
        connection: Connection, record: GovernanceRevision, receipt: GovernanceReceipt
    ) -> None:
        original(connection, record, receipt)
        hits.append("receipt-written")
        raise RuntimeError("governance receipt failpoint")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteGovernanceHistory, "_insert_record", staticmethod(fail))
            failed = await runtime.bus.dispatch(
                request("project/policy/update", "policy-b-fault", payload)
            )
        assert failed.error is not None
        assert hits == ["receipt-written"]
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert domain_snapshot(reopened.ledger.engine) == before
        retried = request("project/policy/update", "policy-b-retry", payload)
        second = value(await reopened.bus.dispatch(retried))
        assert second["policy"]["version"] == first["policy"]["version"] + 1
        assert second["policy"]["policy_digest"] != first["policy"]["policy_digest"]
        after = domain_snapshot(reopened.ledger.engine)
        assert value(await reopened.bus.dispatch(retried)) == second
        assert domain_snapshot(reopened.ledger.engine) == after
    finally:
        reopened.close()
