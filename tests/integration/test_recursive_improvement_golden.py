"""101-R coordinator outputs and effect order, frozen before pure-record extraction.

Reuse the real local automatic-policy fixture and TEST_ONLY legacy evaluator. Scripted sandbox
time, IDs, clock and elapsed meters are deterministic, but no record/digest is normalized away.
Ledger and atomic UoW scopes, static methods, evaluator/await/error paths remain observable.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration import test_a05_bounded_recovery as recovery_fixture
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_improvement_handlers_golden import Recorder, digest, plain
from tests.integration.test_m5_recursive_improvement_atomicity import CountingEvaluator, harness

from thoth.adapters.evaluators import isolated_runner
from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.sandbox import scripted
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork
from thoth.application.services import behavior_context, behavior_models
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.evaluation_producer import EvaluationProducer
from thoth.application.services.improvement_candidate_generator import (
    BoundedBehaviorCandidateGenerator,
)
from thoth.application.services.improvement_evaluation_gate import ImprovementEvaluationGate
from thoth.application.services.improvement_observation_results import ImprovementObservationResults
from thoth.application.services.improvement_runner import ImprovementRunner
from thoth.application.services.recursive_improvement import RecursiveImprovementCoordinator
from thoth.domain.evaluation_run import EvaluationRunError
from thoth.domain.evaluator import ImprovementEvaluationUnavailable
from thoth.domain.improvement import ImprovementEvaluation
from thoth.domain.research_execution import research_work

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/recursive_improvement_golden.json"
SCENARIOS = (
    "untriggered",
    "legacy_success",
    "legacy_rollback",
    "legacy_holdout",
    "legacy_unavailable",
    "legacy_error",
    "legacy_expired",
    "selected_success",
    "selected_hold",
    "selected_cancel",
    "selected_error",
)


def install(patch: pytest.MonkeyPatch, rec: Recorder) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    moments = 0

    def now(_self: Any) -> datetime:
        rec.ticks += 1
        rec.note("clock")
        return datetime(2026, 10, 15, tzinfo=UTC) + timedelta(milliseconds=rec.ticks)

    def new(_self: Any, prefix: str) -> str:
        rec.ids += 1
        rec.note("id", prefix)
        return f"{prefix}:{rec.ids:08d}"

    def timer() -> int:
        rec.timer += 1
        return rec.timer * 1000

    class Moment(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            nonlocal moments
            moments += 1
            return datetime(2026, 10, 15, 1, tzinfo=UTC) + timedelta(milliseconds=moments)

    patch.setattr(SystemClock, "now", now)
    patch.setattr(UuidIdGenerator, "new", new)
    for module in (recovery_fixture, scripted):
        patch.setattr(module, "datetime", Moment)
    for module in (isolated_runner, behavior_context, behavior_models):
        patch.setattr(module, "perf_counter_ns", timer)

    def wrap(owner: Any, name: str, coordinator: bool = False) -> None:
        descriptor = inspect.getattr_static(owner, name)
        static = isinstance(descriptor, staticmethod)
        original = getattr(owner, name)

        def begin(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            if coordinator:
                rec.depth += 1
                if name == "observe":
                    captured.update(
                        owner=args[0], arguments=kwargs.copy(), work=research_work.get()
                    )
            rec.note(
                owner.__name__,
                name,
                [digest(x) for x in (args if static else args[1:])],
                {k: digest(v) for k, v in kwargs.items()},
            )

        def end(result: Any = None, error: BaseException | None = None) -> None:
            if coordinator:
                rec.note(
                    "return" if error is None else "raise",
                    name,
                    digest(result) if error is None else type(error).__name__,
                    "" if error is None else str(error),
                )
                if rec.depth == 1:
                    rec.outputs.append(
                        [
                            name,
                            plain(result)
                            if error is None
                            else {"error": type(error).__name__, "code": str(error)},
                        ]
                    )
                rec.depth -= 1

        if inspect.iscoroutinefunction(original):

            async def asynchronous(*args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = await original(*args, **kwargs)
                except BaseException as exc:
                    end(error=exc)
                    raise
                end(result)
                return result

            traced = asynchronous
        else:

            def synchronous(*args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = original(*args, **kwargs)
                except BaseException as exc:
                    end(error=exc)
                    raise
                end(result)
                return result

            traced = synchronous
        patch.setattr(owner, name, staticmethod(traced) if static else traced)

    for name, descriptor in tuple(RecursiveImprovementCoordinator.__dict__.items()):
        if name != "__init__" and (
            inspect.isfunction(descriptor) or isinstance(descriptor, staticmethod)
        ):
            wrap(RecursiveImprovementCoordinator, name, coordinator=True)
    for owner, names in (
        (ControlRecordService, ("create", "stage")),
        (SqliteControlRecordStore, ("read", "list", "append")),
        (
            SqliteBehaviorArtifactStore,
            ("read", "list", "add", "read_registry", "activate", "rollback"),
        ),
        (BaselineService, ("composite_current_digest",)),
        (BehaviorCandidateService, ("ensure_baseline", "create_candidate")),
        (
            ImprovementEvaluationGate,
            (
                "capture",
                "require_pending",
                "is_current",
                "transition",
                "binding_error",
                "current_active_digest",
                "active_behavior_baseline",
            ),
        ),
        (ImprovementRunner, ("promote", "rollback")),
        (BoundedBehaviorCandidateGenerator, ("generate", "record_evaluation")),
        (ImprovementObservationResults, ("read", "save")),
        (EvaluationProducer, ("select", "run")),
        (CountingEvaluator, ("evaluate",)),
    ):
        for name in names:
            wrap(owner, name)

    def transaction(owner: Any, label: str) -> None:
        original = owner.transaction

        @contextmanager
        def traced(self: Any):
            rec.note(label, "enter", getattr(self, "_immediate", False))
            try:
                with original(self) as entered:
                    yield entered
            except BaseException as exc:
                rec.note(label, "rollback", type(exc).__name__)
                raise
            else:
                rec.note(label, "exit")

        patch.setattr(owner, "transaction", traced)

    transaction(SqliteLedger, "ledger")
    transaction(SqliteAtomicUnitOfWork, "atomic_uow")
    return captured


async def scenario(
    name: str, path: Path, patch: pytest.MonkeyPatch, rec: Recorder, captured: dict[str, Any]
) -> None:
    selected = name.startswith("selected")
    async with harness(path, selected=selected) as h:
        owner: RecursiveImprovementCoordinator = captured["owner"]
        if name == "untriggered":
            projections: tuple[dict[str, object] | None, ...] = (
                None,
                {"terminal_state": "COMPLETED"},
                {"terminal_state": "HOLD", "recovery": {"attempts": []}},
            )
            for projection in projections:
                assert (
                    await owner.observe(
                        project_id=h.project, thread_id=h.thread, r2_projection=projection
                    )
                    is None
                )
                result = owner.observation(h.project, h.thread, projection, None)
                assert result.trigger_state == "NOT_TRIGGERED"
            return
        if name in {
            "legacy_rollback",
            "legacy_holdout",
            "legacy_unavailable",
            "legacy_error",
            "legacy_expired",
        }:
            outcome: dict[str, object] = (
                {"candidate_quality_bps": 4000}
                if name == "legacy_rollback"
                else ({"hidden_holdout_exposed": True} if name == "legacy_holdout" else {})
            )
            evaluator = DeterministicIndependentImprovementEvaluator(outcome)

            def evaluate(self: CountingEvaluator, **kwargs: Any) -> ImprovementEvaluation:
                self.calls += 1
                rec.note("injected_evaluator", name)
                if name == "legacy_unavailable":
                    raise ImprovementEvaluationUnavailable("GOLDEN_EVALUATOR_UNAVAILABLE")
                if name == "legacy_error":
                    raise RuntimeError("GOLDEN_EVALUATOR_FAILURE")
                result = evaluator.evaluate(**kwargs)
                if name == "legacy_expired":
                    rec.ticks += 61_000
                return result

            patch.setattr(CountingEvaluator, "evaluate", evaluate)
        entered = asyncio.Event()
        pending: list[asyncio.Task[Any]] = []
        if name in {"selected_hold", "selected_error", "selected_cancel"}:

            async def run(_self: Any, *_args: Any, **_kwargs: Any) -> Any:
                rec.note("injected_producer", name)
                if name == "selected_hold":
                    raise EvaluationRunError("GOLDEN_PAIR_HELD")
                if name == "selected_error":
                    raise RuntimeError("GOLDEN_PAIR_FAILURE")
                task = asyncio.current_task()
                assert task is not None
                pending.append(task)
                entered.set()
                await asyncio.Future()

            patch.setattr(EvaluationProducer, "run", run)
        if name == "selected_cancel":
            running = asyncio.create_task(h.input("second"))
            try:
                await asyncio.wait_for(entered.wait(), 30)
                pending[0].cancel("GOLDEN_PAIR_CANCELLED")
                with pytest.raises(asyncio.CancelledError):
                    await running
            finally:
                if not running.done():
                    running.cancel()
                    with suppress(asyncio.CancelledError):
                        await running
            return
        response = await h.input("second")
        if name in {"legacy_error", "selected_error"}:
            assert response.error is not None
            return
        result = value(response)["recursive_improvement"]
        expected = {
            "legacy_success": "PROMOTED_LOCAL",
            "legacy_rollback": "ROLLED_BACK",
            "legacy_holdout": "ROLLED_BACK",
            "legacy_expired": "ROLLED_BACK",
            "legacy_unavailable": "HELD",
            "selected_success": "EVALUATED",
            "selected_hold": "HELD",
        }[name]
        assert result["state"] == expected
        reasons = {
            "legacy_rollback": "NO_IMPROVEMENT",
            "legacy_holdout": "HOLDOUT_LEAK",
            "legacy_expired": "TIMEOUT",
            "legacy_unavailable": "GOLDEN_EVALUATOR_UNAVAILABLE",
            "selected_hold": "GOLDEN_PAIR_HELD",
        }
        if name in reasons:
            assert result["rollback_reason"] == reasons[name]
        if name in {"legacy_success", "selected_success"}:
            arguments = dict(captured["arguments"])
            work = captured["work"]
            token = research_work.set(work)
            try:
                cached = await owner.observe(**arguments)
                assert cached is not None and cached.model_dump(mode="json") == result
            finally:
                research_work.reset(token)
            third = value(await h.input("third"))["recursive_improvement"]
            assert third["state"] == ("REJECTED_SAME_DIGEST" if not selected else "HELD")


@pytest.mark.parametrize("name", SCENARIOS)
async def test_recursive_outputs_and_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    captured = install(monkeypatch, rec)
    await scenario(name, tmp_path, monkeypatch, rec, captured)
    assert rec.depth == 0 and rec.outputs and rec.trace
    observed = {"trace": rec.trace, "outputs": rec.outputs}
    if os.environ.get("THOTH_WRITE_GOLDEN") == "1":
        target = Path(os.environ.get("THOTH_GOLDEN_OUT", str(GOLDEN)))
        data: dict[str, Any] = (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        )
        data[name] = observed
        target.write_text(
            json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
    else:
        assert observed == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]
