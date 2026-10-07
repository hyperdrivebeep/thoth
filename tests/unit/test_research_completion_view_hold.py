"""A research result whose closed-loop test is held shows as held, with what the screen needs.

The hold itself is made by the R2 replay guard (test_r2_replay_guard). Here the read side is
checked on its own: what the thread view says about the answer when the stored result carries
"r2_closed_loop" with terminal_state HOLD, and that every other result reads exactly as before."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.services.research_completion_view import completion_view

PENDING: dict[str, Any] = {
    "plan_execution_id": "execution:1",
    "execution_revision": 7,
    "attempt_id": "attempt:1",
    "attempt_state": "SUCCEEDED",
    "observation_completeness": "NOT_ADMITTED",
    "action_id": "action:1",
    "object_id": "object:1",
    "plan_id": "plan:1",
    "input_difference": {
        "same": False,
        "previous_count": 3,
        "current_count": 4,
        "removed": ["a"],
        "added": ["b", "c"],
    },
    "runtime_binding_same": True,
    "exits": ["CLEAR_AND_RERUN"],
}


def view(result: dict[str, Any], *, fresh: bool = True, phase: str = "COMPLETE") -> dict[str, Any]:
    manifest = SimpleNamespace(
        result=result,
        phase=phase,
        completion="TERMINAL",
        record_refs=(),
        source_context_version="2.1.0",
        terminal_reason="BOUNDED_RESEARCH_COMPLETE",
        request_ref=None,
    )
    return completion_view(
        cast(Any, None),
        cast(Any, None),
        None,
        cast(Any, manifest),
        fresh,
        "SUCCEEDED",
        "IDLE",
        None,
    )


def test_a_held_closed_loop_test_makes_the_answer_a_hold_and_carries_the_hold() -> None:
    held = view(
        {
            "answer": "A partial answer",
            "r2_closed_loop": {
                "terminal_state": "HOLD",
                "reason_code": "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED",
                "pending_result": PENDING,
            },
        }
    )
    assert held["answer_outcome"]["state"] == "HOLD"
    assert held["answer_outcome"]["has_answer"] is True
    hold = held["execution_hold"]
    assert hold["schema_version"] == "1.0.0"
    assert hold["reason_code"] == "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED"
    for key in ("plan_execution_id", "execution_revision", "attempt_id", "attempt_state"):
        assert hold[key] == PENDING[key]
    assert hold["input_difference"] == PENDING["input_difference"]
    assert hold["exits"] == ["CLEAR_AND_RERUN"]


@pytest.mark.parametrize(
    "result",
    [
        {"answer": "An answer"},
        {"answer": "An answer", "r2_closed_loop": None},
        {"answer": "An answer", "r2_closed_loop": {"terminal_state": "COMPLETED"}},
    ],
)
def test_a_result_without_a_held_test_reads_exactly_as_before(result: dict[str, Any]) -> None:
    plain = view(result)
    assert plain["answer_outcome"]["state"] == "PARTIAL"
    assert "execution_hold" not in plain


def test_another_hold_is_a_hold_without_anything_to_clear() -> None:
    held = view(
        {
            "answer": "A partial answer",
            "r2_closed_loop": {
                "terminal_state": "HOLD",
                "reason_code": "RESEARCH_TEST_TEMPLATE_UNAVAILABLE",
                "runtime_io_completed": False,
            },
        }
    )
    assert held["answer_outcome"]["state"] == "HOLD"
    hold = held["execution_hold"]
    assert hold["reason_code"] == "RESEARCH_TEST_TEMPLATE_UNAVAILABLE"
    assert hold["exits"] == [] and hold["plan_execution_id"] is None
    assert hold["attempt_id"] is None and hold["input_difference"] is None
