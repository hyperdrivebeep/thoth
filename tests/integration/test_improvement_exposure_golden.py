"""100-R original exposure-service results and effect-order golden.

Public RPCs and real local comparisons prepare evidence; models are scripted. Fix only test
IDs, clocks and measured elapsed timers. Preserve every record field and digest. Static methods
retain their static binding, and all preexisting service methods remain observable after split.
"""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.integration.behavior_exposure_helpers import ExposureHarness, exposure_harness
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_a02_autonomous_acquisition import policy_payload
from tests.integration.test_improvement_handlers_golden import Recorder, digest, plain
from tests.integration.test_m5_exposure_service_atomicity import action_body, current, ready_action

from thoth.adapters.evaluators import isolated_runner
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.services import (
    behavior_context,
    behavior_models,
    improvement_exposure_service,
)
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/improvement_exposure_golden.json"
SCENARIOS = (
    "prepare_shadow",
    "prepare_canary",
    "prepare_guards",
    "budget_guards",
    "approve_start",
    "approval_guards",
    "complete_manual",
    "complete_auto",
    "complete_expired",
    "promote_rollback",
    "currentness_guard",
)


def install(patch: pytest.MonkeyPatch, rec: Recorder) -> None:
    def new(_self: Any, prefix: str) -> str:
        rec.ids += 1
        rec.note("id", prefix)
        return f"{prefix}:{rec.ids:08d}"

    def now(_self: Any) -> datetime:
        rec.ticks += 1
        rec.note("clock")
        return datetime(2026, 10, 15, tzinfo=UTC) + timedelta(milliseconds=rec.ticks)

    def timer() -> int:
        rec.timer += 1
        return rec.timer * 1000

    patch.setattr(UuidIdGenerator, "new", new)
    patch.setattr(SystemClock, "now", now)
    for module in (isolated_runner, behavior_context, behavior_models):
        patch.setattr(module, "perf_counter_ns", timer)

    def wrap(owner: Any, name: str, service: bool = False) -> None:
        descriptor = inspect.getattr_static(owner, name)
        static = isinstance(descriptor, staticmethod)
        original = getattr(owner, name)
        assert not inspect.iscoroutinefunction(original)

        def traced(*args: Any, **kwargs: Any) -> Any:
            if service:
                rec.depth += 1
            visible = args if static else args[1:]
            rec.note(
                owner.__name__,
                name,
                [digest(a) for a in visible],
                {k: digest(v) for k, v in kwargs.items()},
            )
            try:
                result = original(*args, **kwargs)
            except BaseException as exc:
                if service:
                    rec.note("raise", name, type(exc).__name__, str(exc))
                    if rec.depth == 1:
                        rec.outputs.append([name, {"error": type(exc).__name__, "code": str(exc)}])
                raise
            else:
                if service:
                    rec.note("return", name, digest(result))
                    if rec.depth == 1:
                        rec.outputs.append([name, plain(result)])
                return result
            finally:
                if service:
                    rec.depth -= 1

        patch.setattr(owner, name, staticmethod(traced) if static else traced)

    for name in tuple(ImprovementExposureService.__dict__):
        if name != "__init__" and (
            inspect.isfunction(ImprovementExposureService.__dict__[name])
            or isinstance(ImprovementExposureService.__dict__[name], staticmethod)
        ):
            wrap(ImprovementExposureService, name, service=True)
    for owner, methods in (
        (
            SqliteBehaviorExecutionStore,
            ("read", "list", "append", "baseline", "baselines", "promote", "rollback_baseline"),
        ),
        (SqliteControlRecordStore, ("read", "append", "list")),
        (SqliteBehaviorArtifactStore, ("read", "list", "add")),
        (SqliteGovernanceStore, ("read_policy", "list_roles")),
        (ControlRecordService, ("create", "stage")),
        (BehaviorCandidateService, ("create_candidate",)),
        (ImprovementThreadScope, ("require_thread", "require_record")),
        (
            PairedEvaluationService,
            ("require_policy_evidence", "require_current_policy_evidence", "policy_basis"),
        ),
    ):
        for name in methods:
            wrap(owner, name)

    def wrap_function(name: str) -> None:
        original = getattr(improvement_exposure_service, name)

        def traced(*args: Any, **kwargs: Any) -> Any:
            rec.note("authority", name, [digest(a) for a in args])
            return original(*args, **kwargs)

        patch.setattr(improvement_exposure_service, name, traced)

    for name in (
        "require_authenticated_authority",
        "current_authenticated_actor",
        "authenticated_data_scope_allows",
    ):
        wrap_function(name)
    original_tx = SqliteLedger.transaction

    @contextmanager
    def transaction(self: SqliteLedger):
        rec.note("transaction", "enter")
        try:
            with original_tx(self) as entered:
                yield entered
        except BaseException:
            rec.note("transaction", "rollback")
            raise
        else:
            rec.note("transaction", "exit")

    patch.setattr(SqliteLedger, "transaction", transaction)


def prepare_body(h: ExposureHarness) -> dict[str, Any]:
    spec = h.exposure["spec"]
    return {
        "improvement_revision_id": h.pair.proposal["record_id"],
        "evaluation_plan_id": h.pair.plan["record_id"],
        "requested_exposure_state": spec["stage"],
        "scope": {"environment": "LOCAL", "thread_id": h.thread},
        "duration_budget": {
            k: spec[k]
            for k in (
                "duration_seconds",
                "max_requests",
                "max_model_calls",
                "max_billed_cost_microunits",
            )
        },
        "stop_rollback_contract": {"rollback_target_digest": spec["baseline_digest"]},
    }


def approve_body(h: ExposureHarness) -> dict[str, object]:
    return {
        "exposure_id": h.exposure["spec"]["exposure_id"],
        "expected_revision": 1,
        "approved_digest": h.exposure["spec"]["spec_digest"],
        "decision": "APPROVE",
        "actor_ref": "human:policy-owner",
        "role_assignment_ref": h.role,
    }


async def scenario(name: str, path: Path, rec: Recorder) -> None:
    async with exposure_harness(
        path,
        stage="SHADOW" if name == "prepare_shadow" else "CANARY",
        requests=1 if name == "complete_auto" else 3,
    ) as h:
        if name in {"prepare_shadow", "prepare_canary"}:
            assert current(h).state == "PREPARED"
            return
        if name == "prepare_guards":
            base = prepare_body(h)
            for index, changes in enumerate(
                (
                    {"scope": {"environment": "OTHER"}},
                    {"scope": {"unexpected": "field"}},
                    {"scope": {"thread_id": "missing-thread"}},
                    {"stop_rollback_contract": {"rollback_target_digest": "0" * 64}},
                )
            ):
                reply = await h.call(
                    "improvement/exposure/prepare", f"bad-prepare-{index}", {**base, **changes}
                )
                assert reply.error is not None
            return
        if name == "budget_guards":
            base = prepare_body(h)
            for key, bad in (
                ("duration_seconds", True),
                ("max_requests", None),
                ("max_model_calls", "two"),
                ("max_billed_cost_microunits", -1),
            ):
                budget = {**base["duration_budget"]}
                if bad is None:
                    del budget[key]
                else:
                    budget[key] = bad
                reply = await h.call(
                    "improvement/exposure/prepare",
                    "bad-" + key,
                    {**base, "duration_budget": budget},
                )
                assert reply.error is not None
            return
        if name == "approval_guards":
            body = approve_body(h)
            for key, changes in (
                ("digest", {"approved_digest": "0" * 64}),
                ("role", {"role_assignment_ref": "missing-role"}),
            ):
                reply = await h.call(
                    "improvement/exposure/decide", "bad-" + key, {**body, **changes}
                )
                assert reply.error is not None
            await h.arm()
            reply = await h.call(
                "improvement/exposure/start",
                "stale-start",
                {"exposure_id": body["exposure_id"], "expected_revision": 1},
            )
            assert reply.error is not None
            return
        if name == "currentness_guard":
            approved = value(
                await h.call("improvement/exposure/decide", "approve-only", approve_body(h))
            )["exposure"]
            project = value(await h.call("project/read", "policy-before", {}))
            value(
                await h.call(
                    "project/policy/update",
                    "policy-change",
                    {
                        "expected_revision": project["revision"],
                        "payload": policy_payload(allow_connector=False),
                    },
                )
            )
            reply = await h.call(
                "improvement/exposure/start",
                "changed-start",
                {
                    "exposure_id": approved["spec"]["exposure_id"],
                    "expected_revision": approved["revision"],
                },
            )
            assert reply.error is not None
            return
        if name == "approve_start":
            await h.arm()
            assert current(h).state == "ARMED"
            reply = await h.call(
                "improvement/exposure/complete",
                "unobserved",
                action_body(h, "complete", current(h)),
            )
            assert reply.error is not None
            return
        if name == "complete_auto":
            await h.arm()
            value(await h.call("thread/input", "automatic", {"thread_id": h.thread}))
            assert current(h).state == "COMPLETED"
            return
        previous = await ready_action(h, "rollback" if name == "promote_rollback" else "complete")
        if name == "complete_expired":
            rec.ticks += 600_000
            result = value(
                await h.call(
                    "improvement/exposure/complete", "expired", action_body(h, "complete", previous)
                )
            )["exposure"]
            assert result["state"] == "ROLLED_BACK"
            assert result["reason_code"] == "BEHAVIOR_EXPOSURE_EXPIRED"
            return
        if name == "complete_manual":
            await h.finish_trial()
        rolled = value(
            await h.call(
                "improvement/exposure/rollback", "rollback", action_body(h, "rollback", current(h))
            )
        )["exposure"]
        assert rolled["state"] == "ROLLED_BACK"
        repeated = value(
            await h.call(
                "improvement/exposure/rollback",
                "repeat-rollback",
                action_body(h, "rollback", current(h)),
            )
        )["exposure"]
        assert repeated == rolled


@pytest.mark.parametrize("name", SCENARIOS)
async def test_exposure_service_records_and_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    await scenario(name, tmp_path, rec)
    observed = {"trace": rec.trace, "outputs": rec.outputs}
    assert rec.depth == 0 and rec.trace and rec.outputs
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
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))[name]
        assert observed == expected
