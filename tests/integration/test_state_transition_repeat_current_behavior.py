"""How a state-changing command answers a repeat under a new key (V-S; D-S1 and D-S2 changed it).

Nine commands change a state: thread pause/resume/stop, investigation pause/stop, closure
decide/reopen, revision baseline decide and action authorization decide. For all of them an
"already in that state" repeat now returns the current state with unchanged=true, writes no domain
row and sends no notification (V-S pinned the opposite; D-S1 changed the thread and investigation
commands, D-S2 the rest). Also pinned here:
 - an investigation that already ended keeps its verdict and result whatever stop reason comes
   later, and is not paused;
 - authorization: a repeat of the same role's vote while the approval is PENDING is unchanged; once
   the decision is final a repeat is still refused ("authorization is not pending");
 - the research continuation path (thread pause/resume/stop while a research request exists):
   a repeated pause is answered unchanged, "already terminal" is answered once the request has
   finished, a repeated stop is answered unchanged without cancelling again (and still cancels a
   request a cut-off first stop left running), and a second resume while the first is still
   running is answered unchanged and starts no second research (D-S3).
The first run of each command, the same-key replay and the guard refusals are not pinned here (the
existing flow tests cover them and must keep passing unchanged). The flow tests are run as they are
and the repeat is sent from a wrapper around the bus (see repeat_probe.py). Fake models only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from tests.integration.repeat_probe import RepeatProbe
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import domain_snapshot, request, value
from tests.integration.test_action_full_rpc import (
    test_action_decision_plan_policy_and_content_bound_authorization as action_flow,
)
from tests.integration.test_investigation_full_rpc import (
    test_investigation_plan_checkpoint_audit_and_stop_reducer as investigation_flow,
)
from tests.integration.test_receipt_closure_export_full_rpc import (
    test_receipt_closure_and_local_export_without_external_release as closure_flow,
)
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.test_revision_full_rpc import (
    test_revision_atomic_changeset_merge_restore_and_baseline as baseline_flow,
)
from tests.integration.test_thread_full_catalog_rpc import (
    test_thread_list_activity_checkpoint_steer_fork_and_stop as thread_flow,
)

from thoth.apps.runtime import AppRuntime

Flow = Callable[[Path], Awaitable[None]]

# method -> why the repeat is answered unchanged
_UNCHANGED: list[tuple[str, Flow, dict[str, str]]] = [
    (
        "thread",
        thread_flow,
        {
            "thread/pause": "THREAD_ALREADY_PAUSED",
            "thread/resume": "THREAD_ALREADY_RESUMED",
            "thread/stop": "THREAD_ALREADY_STOPPED",
        },
    ),
    (
        "investigation",
        investigation_flow,
        {
            "investigation/pause": "INVESTIGATION_ALREADY_PAUSED",
            "investigation/stop": "INVESTIGATION_ALREADY_ENDED",
        },
    ),
    (
        "closure",
        closure_flow,
        {
            "closure/decide": "CLOSURE_ALREADY_DECIDED",
            "closure/reopen": "CLOSURE_ALREADY_REOPENED",
        },
    ),
    ("baseline", baseline_flow, {"revision/baseline/decide": "BASELINE_ALREADY_DECIDED"}),
    (
        "authorization",
        action_flow,
        {"action/authorization/decide": "AUTHORIZATION_VOTE_ALREADY_RECORDED"},
    ),
]


@pytest.mark.parametrize(
    ("name", "flow", "reasons"), _UNCHANGED, ids=[name for name, _flow, _reasons in _UNCHANGED]
)
async def test_a_repeat_of_the_same_state_returns_it_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    flow: Flow,
    reasons: dict[str, str],
) -> None:
    probe = RepeatProbe(frozenset(reasons))
    probe.install(monkeypatch)
    await flow(tmp_path)
    for method, reason in reasons.items():
        repeats = probe.results[method]
        assert repeats, (name, method)
        for repeat in repeats:
            if not repeat.accepted:
                # Only a final authorization decision is still refused, and refused without writing.
                assert method == "action/authorization/decide", (method, repeat)
                assert repeat.error_message == "authorization is not pending", repeat
            else:
                assert repeat.unchanged_reason == reason, (method, repeat)
            assert not repeat.changed_tables and not repeat.added_rows, (method, repeat)
            assert repeat.notifications == (), (method, repeat)
    if name == "authorization":
        refused = [r for r in probe.results["action/authorization/decide"] if not r.accepted]
        assert refused, "a final decision must still refuse a repeat"


async def test_a_different_decision_or_scope_is_still_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the same decision (or the same reopening scope) is answered unchanged."""

    def change(method: str, params: dict[str, Any]) -> None:
        if method == "closure/decide":
            params["decision"] = "REJECT"
        else:
            params["child_scope_ref"] = "scope:another-child"

    probe = RepeatProbe(frozenset({"closure/decide", "closure/reopen"}), change=change)
    probe.install(monkeypatch)
    await closure_flow(tmp_path)
    for method in ("closure/decide", "closure/reopen"):
        for repeat in probe.results[method]:
            assert repeat.accepted and repeat.unchanged_reason is None, (method, repeat)
            assert repeat.changed_tables == {"control_records"}, (method, repeat)


async def test_a_later_stop_keeps_the_verdict_of_an_investigation_that_already_ended(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "p",
                    {"project_id": "p", "name": "X", "cutoff_at": "2026-08-31T00:00:00Z"},
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "t",
                    {
                        "project_id": "p",
                        "thread_id": "th",
                        "problem": "Why?",
                        "scope": {"workstream": "x"},
                    },
                )
            )
        )
        listed = value(
            await runtime.bus.dispatch(
                request("investigation/list", "l", {"project_id": "p", "thread_id": "th"})
            )
        )
        investigation_id = listed["investigations"][0]["investigation_id"]

        async def send(method: str, key: str, **fields: object) -> dict[str, Any]:
            return value(
                await runtime.bus.dispatch(
                    request(
                        method,
                        key,
                        {"project_id": "p", "investigation_id": investigation_id, **fields},
                    )
                )
            )

        first = await send("investigation/stop", "stop-1", reason="SUFFICIENT")
        converged = first["investigation"]
        assert converged["domain_state"] == "CONVERGED"
        assert converged["result"]["stop_reason"] == "SUFFICIENT"
        assert "unchanged" not in first
        # A later stop (any supported reason) answers with the finished state and changes nothing.
        later = await send("investigation/stop", "stop-2", reason="USER_STOPPED")
        assert later["unchanged"] is True
        assert later["unchanged_reason"] == "INVESTIGATION_ALREADY_ENDED"
        assert later["investigation"] == converged
        # An unsupported reason is still refused, as before.
        refused = await runtime.bus.dispatch(
            request(
                "investigation/stop",
                "stop-3",
                {
                    "project_id": "p",
                    "investigation_id": investigation_id,
                    "reason": "NOT_A_REASON",
                },
            )
        )
        assert refused.error is not None
        # An ended investigation is not paused either.
        paused = await send("investigation/pause", "pause")
        assert paused["unchanged"] is True
        assert paused["investigation"] == converged
        assert paused["investigation"]["execution_state"] == "IDLE"
    finally:
        runtime.close()


async def _thread_state(runtime: AppRuntime, scope: dict[str, object]) -> tuple[Any, Any]:
    read = value(await runtime.bus.dispatch(request("thread/read", "read-" + str(uuid4()), scope)))
    return read["execution_state"], read["revision"]


async def test_the_research_path_answers_a_repeated_pause_and_stop_unchanged(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Pause then resume", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 8)
        scope: dict[str, object] = {"project_id": "p", "thread_id": accepted["thread_id"]}

        async def send(method: str, key: str) -> dict[str, Any]:
            return value(await runtime.bus.dispatch(request(method, key, scope)))

        # While the research still runs, a second pause is answered unchanged and writes nothing.
        first = await send("thread/pause", "pause-1")
        _state, revision = await _thread_state(runtime, scope)
        assert first["pause_accepted"] is True and first["execution_state"] == "PAUSE_PENDING"
        second = await send("thread/pause", "pause-2")
        assert second["pause_accepted"] is True and second["execution_state"] == "PAUSE_PENDING"
        assert second["unchanged"] is True and second["unchanged_reason"] == "THREAD_ALREADY_PAUSED"
        assert await _thread_state(runtime, scope) == ("PAUSE_PENDING", revision)
        model.release.set()
        await asyncio.wait_for(runtime.bus.drain(), 12)
        state, revision = await _thread_state(runtime, scope)
        assert state == "PAUSED"
        # Already paused: a pause is answered unchanged and the revision does not move.
        third = await send("thread/pause", "pause-3")
        assert third["pause_accepted"] is True and third["execution_state"] == "PAUSED"
        assert third["unchanged"] is True and third["unchanged_reason"] == "THREAD_ALREADY_PAUSED"
        assert await _thread_state(runtime, scope) == ("PAUSED", revision)

        await send("thread/resume", "resume-1")
        await asyncio.wait_for(runtime.bus.drain(), 12)
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None and operation.state.value == "SUCCEEDED"
        finished = await _thread_state(runtime, scope)
        # Once the request has finished, pause and resume answer "already terminal", write nothing.
        assert await send("thread/resume", "resume-2") == {
            "resume_allowed": False,
            "reason": "OPERATION_ALREADY_TERMINAL",
        }
        assert await send("thread/pause", "pause-4") == {
            "pause_accepted": False,
            "reason": "OPERATION_ALREADY_TERMINAL",
        }
        assert await _thread_state(runtime, scope) == finished
        # The first stop cancels the request; a repeated stop answers unchanged, cancels nothing
        # new (cancelled=False means "this request cancelled nothing") and writes nothing.
        stopped = await send("thread/stop", "stop-1")
        assert stopped["lifecycle"] == "STOPPED" and stopped["cancelled"] is True
        assert "unchanged" not in stopped
        after_stop = await _thread_state(runtime, scope)
        assert after_stop == (finished[0], finished[1] + 1)
        again = await send("thread/stop", "stop-2")
        assert again["lifecycle"] == "STOPPED" and again["cancelled"] is False
        assert again["unchanged"] is True and again["unchanged_reason"] == "THREAD_ALREADY_STOPPED"
        assert await _thread_state(runtime, scope) == after_stop
    finally:
        runtime.close()


async def _research_calls(tmp_path: Path, *, resumes: int) -> tuple[int, str, Any, dict[str, Any]]:
    """Pause a running research request, then resume it `resumes` times while it runs again."""
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Resume twice", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 8)
        scope: dict[str, object] = {"project_id": "p", "thread_id": accepted["thread_id"]}
        value(await runtime.bus.dispatch(request("thread/pause", "pause", scope)))
        model.release.set()
        await asyncio.wait_for(runtime.bus.drain(), 12)
        model.release.clear()  # the resumed run blocks again, so the next resume meets it running
        last: dict[str, Any] = {}
        for index in range(resumes):
            last = value(
                await runtime.bus.dispatch(request("thread/resume", f"resume-{index}", scope))
            )
            await asyncio.sleep(0.5)
        model.release.set()
        await asyncio.wait_for(runtime.bus.drain(), 12)
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None
        state = await _thread_state(runtime, scope)
        return len(model.calls), operation.state.value, state, last
    finally:
        runtime.close()


async def test_a_second_resume_while_the_first_is_running_starts_no_second_research(
    tmp_path: Path,
) -> None:
    once_calls, once_state, once_thread, once_last = await _research_calls(
        tmp_path / "once", resumes=1
    )
    twice_calls, twice_state, twice_thread, twice_last = await _research_calls(
        tmp_path / "twice", resumes=2
    )
    assert once_state == twice_state == "SUCCEEDED"
    # No duplicate model work, and the second resume (met while the first still runs) is answered
    # unchanged: it writes nothing and dispatches nothing, so the thread ends as after one resume.
    assert twice_calls == once_calls
    assert "unchanged" not in once_last
    assert twice_last["unchanged"] is True
    assert twice_last["unchanged_reason"] == "THREAD_ALREADY_RESUMED"
    assert twice_last["resume_allowed"] is True
    assert twice_thread == once_thread


async def test_a_second_stop_finishes_the_cancel_that_a_cut_off_first_stop_did_not_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.storage.operations import SqliteOperationStore

    class Died(BaseException):
        pass

    def die(*_args: object, **_kwargs: object) -> None:
        raise Died

    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "Stop twice", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 8)
        scope: dict[str, object] = {"project_id": "p", "thread_id": accepted["thread_id"]}
        operation_id = str(accepted["operation_id"])

        def operation_state() -> str:
            operation = runtime.bus.read_operation(operation_id)
            assert operation is not None
            return operation.state.value

        # The first stop commits the thread as STOPPED, then is cut off before it can cancel.
        with monkeypatch.context() as patch:
            patch.setattr(SqliteOperationStore, "cancel", die)
            with pytest.raises(Died):
                await runtime.bus.dispatch(request("thread/stop", "stop-1", scope))
        read = value(await runtime.bus.dispatch(request("thread/read", "read-1", scope)))
        assert read["lifecycle"] == "STOPPED"
        assert operation_state() == "RUNNING"
        stopped_revision = read["revision"]

        # A second stop does not skip the cancel just because the thread is already stopped.
        second = value(await runtime.bus.dispatch(request("thread/stop", "stop-2", scope)))
        assert second["cancelled"] is True and second["cancel_operation_id"] == operation_id
        assert "unchanged" not in second  # announced as a normal stop, the cancel is not hidden
        await asyncio.wait_for(runtime.bus.drain(), 12)
        assert operation_state() == "CANCELLED"
        read = value(await runtime.bus.dispatch(request("thread/read", "read-2", scope)))
        assert read["revision"] == stopped_revision  # the thread itself was not written again

        # Once the request is cancelled, a further stop writes nothing and cancels nothing.
        before = domain_snapshot(runtime.ledger.engine)
        third = value(await runtime.bus.dispatch(request("thread/stop", "stop-3", scope)))
        assert third["unchanged"] is True and third["cancelled"] is False
        assert domain_snapshot(runtime.ledger.engine) == before
        assert operation_state() == "CANCELLED"
    finally:
        runtime.close()
