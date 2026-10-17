"""R2ClosedLoopCoordinator golden (98): what the loop returns and which ports it calls.

Scenarios run on a real runtime through thread/input (the only entry) with the scripted A04/A05
model and sandbox adapters (no real model, no real sandbox), a deterministic id generator, clock
and sandbox time. The trace holds the calls made while a coordinator method is running: method
entries, exits and exceptions with argument and result digests, sandbox, commit, research, test
lifecycle, compiler and ledger calls, the id and clock sequence and every transaction entry. The
golden file is generated from the pre-move coordinator (THOTH_WRITE_GOLDEN=1), reviewed and frozen:
moving the pure record construction out of it must leave projections, call order, counts, awaits and
digests unchanged. A few scenarios inject a failure at a boundary with a fake raise; nothing is
written by the injection itself.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from tests.integration import test_a05_bounded_recovery as a05
from tests.integration.storage_coverage_helpers import value as ok_value
from tests.integration.test_a04_r2_closed_loop import CallbackSandbox, prepare_a04, request
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery
from tests.integration.test_a07_semantic_three_way_merge import commit
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.sandbox import scripted
from thoth.adapters.storage.schema import semantic_revisions
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.application.services.r2_sandbox_compiler import R2SandboxSpecCompiler
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.sandbox_service import SandboxService
from thoth.domain.sandbox import SandboxErrorCode, SandboxExecutionState, SandboxFailure

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "r2_closed_loop_golden.json"
BASE = datetime(2026, 10, 15, tzinfo=UTC)
SCENARIOS = [
    "success",
    "policy_blocked",
    "template_hold",
    "stale_head",
    "recovery_transient",
    "recovery_semantic",
    "recovery_code",
    "recovery_ambiguous",
    "recovery_budget",
    "compile_failure",
    "run_failure",
    "admission_failure",
    "prepared_success",
    "prepared_transient_retry",
    "prepared_failure",
    "prepared_finalization_failure",
    "prepared_assessment_rejected",
    "prepare_sandbox_failure",
    "prepare_value_error",
]
COORDINATOR_METHODS = (
    "execute_if_required",
    "_completed_execution",
    "_current_research_refs",
    "_prepare_test",
    "_measurement_assessments",
    "_record_test_hold",
    "_compile_or_hold",
    "_commit_outcome",
    "_admission_hold",
    "_recovery_policy",
    "_stale_head_execution",
    "_run_or_hold",
    "_current_portfolio",
    "_current_action_plan",
    "_failure_execution",
)
PORTS: list[tuple[Any, str, tuple[str, ...]]] = [
    (SandboxService, "X", ("run", "accepting_observation", "reject_observation")),
    (RevisionCommitService, "C", ("commit",)),
    (
        ResearchIdentityService,
        "R",
        (
            "context",
            "stage_cycle",
            "persist_cycle",
            "stage_actions",
            "read_portfolio",
            "read_action_plan",
        ),
    ),
    (R2TestLifecycle, "T", ("prepare", "prepare_assessments", "complete", "hold", "retry")),
    (R2SandboxSpecCompiler, "P", ("compile",)),
    (SqliteLedger, "L", ("read_heads", "read_revision_by_digest", "transaction")),
]


def plain(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    if isinstance(item, datetime):
        return item.isoformat()
    if isinstance(item, dict):
        return {str(k): plain(v) for k, v in cast(dict[Any, Any], item).items()}
    if isinstance(item, (list, tuple, set, frozenset)):
        return [plain(v) for v in cast(Any, item)]
    return item


def summ(item: Any) -> Any:
    if item is None or isinstance(item, (str, int, float, bool)):
        return item
    if isinstance(item, datetime):
        return item.isoformat()
    kind = type(item).__name__
    if not isinstance(item, (BaseModel, dict, list, tuple, set, frozenset)):
        return kind
    text = json.dumps(plain(cast(Any, item)), sort_keys=True, default=str)
    return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


class Recorder:
    def __init__(self) -> None:
        self.on = False
        self.depth = 0
        self.log: list[list[Any]] = []
        self.results: list[list[Any]] = []
        self.ids = 0
        self.ticks = 0
        self.moments = 0

    def note(self, *entry: Any) -> None:
        if self.on and self.depth > 0:
            self.log.append(list(entry))

    def result(self, name: str, item: Any) -> None:
        self.results.append([name, plain(item)])


def arguments(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Any]:
    return [*[summ(a) for a in args], *[f"{k}={summ(v)}" for k, v in sorted(kwargs.items())]]


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

    class Moment(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            rec.moments += 1
            return datetime(2026, 10, 15, 1, tzinfo=UTC) + timedelta(milliseconds=rec.moments)

    monkeypatch.setattr(scripted, "datetime", Moment)
    monkeypatch.setattr(a05, "datetime", Moment)

    def wrap(owner: Any, short: str, name: str, *, enter: bool) -> None:
        original = getattr(owner, name)
        asynchronous = inspect.iscoroutinefunction(original)

        def begin(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            if enter:
                rec.depth += 1
            rec.note(
                short,
                "enter" if enter else name,
                *([name] if enter else []),
                *arguments(args, kwargs),
            )

        def end(result: Any = None, error: BaseException | None = None) -> None:
            if not enter:
                return
            if error is None:
                rec.note(short, "exit", name, summ(result))
            else:
                rec.note(short, "raise", name, type(error).__name__, str(error))
            rec.depth -= 1

        if asynchronous:

            async def traced_async(self: Any, *args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = await original(self, *args, **kwargs)
                except BaseException as error:
                    end(error=error)
                    raise
                end(result)
                return result

            monkeypatch.setattr(owner, name, traced_async)
            return

        def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
            begin(args, kwargs)
            try:
                result = original(self, *args, **kwargs)
            except BaseException as error:
                end(error=error)
                raise
            end(result)
            return result

        monkeypatch.setattr(owner, name, traced)

    for method in COORDINATOR_METHODS:
        wrap(R2ClosedLoopCoordinator, "S", method, enter=True)
    for owner, short, names in PORTS:
        for method in names:
            wrap(owner, short, method, enter=False)


def reference_part(response: Any) -> dict[str, Any]:
    if response.error is not None:
        error = response.error
        return {"error": {"code": error.code, "message": error.message, "data": plain(error.data)}}
    result = ok_value(response)
    return {"r2_closed_loop": result.get("r2_closed_loop")}


async def traced_input(rec: Recorder, runtime: Any, project: str, thread: str, key: str) -> None:
    rec.on = True
    try:
        response = await runtime.bus.dispatch(
            request("thread/input", key, {"project_id": project, "thread_id": thread})
        )
    finally:
        rec.on = False
    rec.result("response", reference_part(response))


def failure(message: str) -> SandboxFailure:
    return SandboxFailure(SandboxErrorCode.INPUT_INVALID, message)


async def stale_head(rec: Recorder, path: Path) -> None:
    sandbox = CallbackSandbox()
    runtime, _sandbox, project_id, thread_id = await prepare_a04(
        path, allow_sandbox=True, sandbox=sandbox
    )

    async def advance_portfolio_head() -> None:
        heads = dict(runtime.ledger.read_heads(project_id))
        listed = ok_value(
            await runtime.bus.dispatch(
                request("hypothesis/portfolio/list", "g98-portfolios", {"project_id": project_id})
            )
        )
        portfolio_id = str(cast(list[dict[str, Any]], listed["portfolios"])[0]["portfolio_id"])
        head_key = f"HYPOTHESIS:{portfolio_id}"
        parent = heads[head_key]
        revision = runtime.ledger.read_revision_by_digest(project_id, parent)
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        content = cast(dict[str, Any], snapshot.content)
        proposed = ok_value(
            await runtime.bus.dispatch(
                request(
                    "revision/propose",
                    "g98-stale-propose",
                    {
                        "project_id": project_id,
                        "aggregate_id": portfolio_id,
                        "aggregate_type": "HYPOTHESIS",
                        "parent_revision_digests": [parent],
                        "candidate_content": {
                            **content,
                            "quality_gaps": ["concurrent authorized R2 update"],
                        },
                        "reason": "concurrent authorized A04 update",
                        "evidence_refs": [],
                        "actor_or_agent_ref": "agent:a04:concurrent",
                        "expected_head_digest": parent,
                        "candidate_schema_version": "1.0.0",
                    },
                )
            )
        )
        proposal = cast(dict[str, Any], proposed["proposal"])
        await commit(
            runtime,
            project_id=project_id,
            expected_heads=heads,
            proposal_digest=str(proposal["record_digest"]),
            suffix="g98-stale",
        )
        with runtime.ledger.engine.connect() as connection:
            connection.execute(select(func.count()).select_from(semantic_revisions))

    sandbox.callback = advance_portfolio_head
    try:
        await traced_input(rec, runtime, project_id, thread_id, "g98-stale")
    finally:
        runtime.close()


async def scenario(name: str, path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder) -> None:
    if name == "stale_head":
        await stale_head(rec, path)
        return
    if name.startswith("recovery_"):
        results = {
            "recovery_transient": (
                (SandboxExecutionState.BOOT_FAILED, "TRANSIENT_INFRA: boot race"),
                (SandboxExecutionState.SUCCEEDED, None),
            ),
            "recovery_semantic": ((SandboxExecutionState.FAILED, "SEMANTIC: invalid assumption"),),
            "recovery_code": (
                (SandboxExecutionState.FAILED, "CODE: deterministic syntax failure"),
            ),
            "recovery_ambiguous": (
                (SandboxExecutionState.FAILED, "AMBIGUOUS_EXTERNAL: result unknown"),
            ),
            "recovery_budget": (
                (SandboxExecutionState.BOOT_FAILED, "TRANSIENT_INFRA: one"),
                (SandboxExecutionState.BOOT_FAILED, "TRANSIENT_INFRA: two"),
            ),
        }[name]
        retries = 1 if name in {"recovery_transient", "recovery_budget"} else 2
        adapter = RecoverySandboxAdapter(results)
        runtime, project, thread = await prepare_recovery(path, name, adapter, max_retries=retries)
        try:
            await traced_input(rec, runtime, project, thread, "g98-" + name)
        finally:
            runtime.close()
        return
    if name.startswith(("prepared_", "prepare_")):
        states: tuple[SandboxExecutionState, ...] = ()
        retries = 0
        if name == "prepared_transient_retry":
            states = (SandboxExecutionState.BOOT_FAILED, SandboxExecutionState.SUCCEEDED)
            retries = 1
        if name == "prepared_failure":
            states = (SandboxExecutionState.FAILED,)
        runtime, _sandbox, project, thread, _contract = await prepare_measurement(
            path, monkeypatch, states=states, max_transient_retries=retries
        )
        try:
            if name == "prepared_finalization_failure":

                def complete(self: Any, *args: Any, **kwargs: Any) -> Any:
                    raise RuntimeError("injected finalization failure")

                monkeypatch.setattr(R2TestLifecycle, "complete", complete)
            elif name == "prepared_assessment_rejected":

                def assessments(self: Any, *args: Any, **kwargs: Any) -> Any:
                    raise ValueError("RESEARCH_TEST_ASSESSMENT_INVALID")

                monkeypatch.setattr(R2TestLifecycle, "prepare_assessments", assessments)
            elif name == "prepare_sandbox_failure":

                def prepare(self: Any, *args: Any, **kwargs: Any) -> Any:
                    raise failure("prepare failure")

                monkeypatch.setattr(R2TestLifecycle, "prepare", prepare)
            elif name == "prepare_value_error":

                def prepare_value(self: Any, *args: Any, **kwargs: Any) -> Any:
                    raise ValueError("RESEARCH_TEST_INPUT_BAD")

                monkeypatch.setattr(R2TestLifecycle, "prepare", prepare_value)
            await traced_input(rec, runtime, project, thread, "g98-" + name)
        finally:
            runtime.close()
        return
    allow = name not in {"policy_blocked", "template_hold"}
    runtime, _sandbox, project, thread = await prepare_a04(
        path, allow_sandbox=allow, include_template=name != "template_hold"
    )
    try:
        if name == "compile_failure":

            def compile_spec(self: Any, **kwargs: Any) -> Any:
                raise failure("compile failure")

            monkeypatch.setattr(R2SandboxSpecCompiler, "compile", compile_spec)
        elif name == "run_failure":

            async def run(self: Any, spec: Any) -> Any:
                raise failure("run failure")

            monkeypatch.setattr(SandboxService, "run", run)
        elif name == "admission_failure":

            def basis(self: Any, *args: Any, **kwargs: Any) -> Any:
                raise failure("admission basis changed")

            monkeypatch.setattr(SandboxService, "_validate_admission_basis", basis)
        await traced_input(rec, runtime, project, thread, "g98-" + name)
    finally:
        runtime.close()


def load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}


@pytest.mark.parametrize("name", SCENARIOS)
async def test_r2_closed_loop_results_and_call_order_are_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    await scenario(name, tmp_path / "workspace", monkeypatch, rec)
    observed: dict[str, Any] = {"trace": rec.log, "results": rec.results}
    assert observed["trace"], "the traced step made no coordinator call"
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
    assert observed["results"] == expected["results"]
