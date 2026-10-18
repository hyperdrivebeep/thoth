"""Research pause/resume: when a repeat is answered unchanged and when it is not (D-S3).

A repeated pause is unchanged only when the thread AND the attempt are already in the pause that
would be written. A resume is unchanged only when the request is already running under a LIVE
worker (attempt RUNNING, lease HELD and unexpired, holder known to be alive). A dead, unknown or
expired holder, a released or missing lease, or a paused attempt still takes the recovery path:
the thread is written and the attempt is dispatched again. Stand-in host; no storage or model.
"""

# pyright: reportUnknownLambdaType=false
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from thoth.application.services.research_controls import ResearchControls
from thoth.domain.enums import OperationState, ThreadExecutionState

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SCOPE: dict[str, Any] = {"project_id": "p", "thread_id": "th"}


class _Copy(SimpleNamespace):
    def model_copy(self, *, update: dict[str, Any]) -> _Copy:
        return _Copy(**{**vars(self), **update})


def _lease(*, state: str = "HELD", expires_in: int = 300) -> _Copy:
    return _Copy(
        state=state,
        expires_at=NOW + timedelta(seconds=expires_in),
        process_id=1,
        process_marker="m",
        worker_id="w",
    )


class _Harness:
    def __init__(
        self,
        *,
        thread_state: ThreadExecutionState,
        attempt_status: str,
        lease: _Copy | None,
        alive: bool | None = True,
    ) -> None:
        self.updates: list[Any] = []
        self.journaled: list[Any] = []
        self.replayed = 0
        thread = _Copy(execution_state=thread_state, revision=3)
        attempt = _Copy(status=attempt_status, external_effect_state="NONE")
        operation = SimpleNamespace(state=OperationState.RUNNING, project_id="p", operation_id="op")

        @contextmanager
        def transaction() -> Any:
            yield

        def update(value: Any, *, expected_revision: int) -> bool:
            self.updates.append((value, expected_revision))
            return True

        def replay(_attempt: Any, _operation: Any) -> Any:
            self.replayed += 1
            return SimpleNamespace(value={"replayed": True}, continue_execution=lambda: None)

        host = SimpleNamespace(
            records=SimpleNamespace(
                ledger=SimpleNamespace(transaction=transaction),
                clock=SimpleNamespace(now=lambda: NOW),
                journal_read=lambda *_a, **_k: lease,
                journal=lambda *args: self.journaled.append(args),
            ),
            threads=SimpleNamespace(read=lambda _id: thread, update=update),
            leases=SimpleNamespace(worker=SimpleNamespace(is_alive=lambda *_a: alive)),
            accepted_value=lambda _attempt: {"operation_id": "op"},
            replay_attempt=replay,
        )
        controls: Any = ResearchControls(host)  # pyright: ignore[reportArgumentType]
        controls.current = lambda _value: (attempt, operation)
        self.controls = controls

    @property
    def wrote(self) -> bool:
        return bool(self.updates or self.journaled)


_PAUSED = ThreadExecutionState.PAUSED
_PENDING = ThreadExecutionState.PAUSE_PENDING
_IDLE = ThreadExecutionState.IDLE
_RUNNING = ThreadExecutionState.RUNNING


@pytest.mark.parametrize(
    ("thread_state", "attempt_status", "lease", "unchanged"),
    [
        (_PENDING, "PAUSE_REQUESTED", _lease(), True),  # a pause is already pending
        (_PAUSED, "PAUSED", None, True),  # already paused, nothing running
        (_IDLE, "RUNNING", _lease(), False),  # running: the pause is a real change
        (_PENDING, "RUNNING", _lease(), False),  # thread says pending, attempt does not
        (
            _PENDING,
            "PAUSE_REQUESTED",
            None,
            False,
        ),  # pending but nothing runs: it settles to PAUSED
        (_PAUSED, "PAUSED", _lease(), False),  # paused thread but a worker holds the lease
    ],
)
async def test_a_repeated_pause_is_unchanged_only_when_thread_and_attempt_already_match(
    thread_state: ThreadExecutionState,
    attempt_status: str,
    lease: _Copy | None,
    unchanged: bool,
) -> None:
    harness = _Harness(thread_state=thread_state, attempt_status=attempt_status, lease=lease)
    answered = await harness.controls.pause(SCOPE)
    assert answered["pause_accepted"] is True
    assert ("unchanged" in answered) is unchanged
    assert harness.wrote is not unchanged


@pytest.mark.parametrize(
    ("attempt_status", "lease", "alive", "unchanged"),
    [
        ("RUNNING", _lease(), True, True),  # running under a live worker
        ("RUNNING", _lease(), False, False),  # holder is dead: recover
        ("RUNNING", _lease(), None, False),  # liveness unknown: recover
        ("RUNNING", _lease(expires_in=-1), True, False),  # lease expired
        ("RUNNING", _lease(state="RELEASED"), True, False),  # lease released
        ("RUNNING", None, True, False),  # no lease at all
        ("PAUSE_REQUESTED", _lease(), True, False),  # a pause was requested: resume clears it
        ("PAUSED", None, True, False),  # paused: resume starts the run
    ],
)
async def test_a_repeated_resume_is_unchanged_only_while_a_live_worker_runs_it(
    attempt_status: str, lease: _Copy | None, alive: bool | None, unchanged: bool
) -> None:
    harness = _Harness(thread_state=_IDLE, attempt_status=attempt_status, lease=lease, alive=alive)
    answered = await harness.controls.resume(SCOPE)
    if unchanged:
        assert answered["unchanged"] is True
        assert answered["unchanged_reason"] == "THREAD_ALREADY_RESUMED"
        assert answered["resume_allowed"] is True and answered["execution_state"] == "IDLE"
        assert not harness.wrote and harness.replayed == 0
    else:
        # The recovery path: the thread is written and the attempt is dispatched again.
        assert harness.updates and harness.journaled and harness.replayed == 1
