"""99-R original handler outputs, notification keys and effect order.

2026-10-26: text parser 1.2 metadata and dependent digests were refreshed after an exact
1.1 parser control and unchanged call/result shapes. Equality checks remain exact.

All public handler calls (including harness setup) are recorded. Real local pair execution and
scripted exposure work prepare valid evidence; clocks, IDs and the pair elapsed timer are fixed
before setup. No stored digest is normalized or discarded. These are local regression fixtures,
not evidence of live model quality or elapsed-time performance.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.behavior_exposure_helpers import exposure_harness
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.storage_coverage_helpers import prepare_project, request, value
from tests.integration.test_m5_improvement_handlers_atomicity import (
    decision_body,
    proposal_body,
    ready_promotion,
)

from thoth.adapters.evaluators import isolated_runner
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.commands import improvement_full
from thoth.application.commands.improvement_full import ImprovementHandlers
from thoth.application.services import behavior_context, behavior_models
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.protocol.notifications import notifications_for

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/improvement_handlers_golden.json"
SCENARIOS = (
    "draft_chain",
    "proposal_guards",
    "plan_hidden",
    "plan_self",
    "plan_baseline",
    "assess_pair",
    "assess_critical",
    "assess_unresolved",
    "expose_shadow",
    "promote_approve",
    "promote_reject",
    "promotion_guards",
    "exposure_error",
)
METHODS = (
    "list",
    "read",
    "evaluation_read",
    "exposure_read",
    "shadow_read",
    "canary_read",
    "promotion_read",
    "audit_read",
    "propose",
    "revise",
    "evaluation_plan",
    "evaluation_assess",
    "exposure_prepare",
    "promotion_prepare",
    "promotion_decide",
    "rollback_prepare",
    "retire_propose",
    "_measured_result",
    "_improvement",
    "_record",
)


def plain(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    if isinstance(item, datetime):
        return item.isoformat()
    if isinstance(item, dict):
        return {str(k): plain(v) for k, v in cast(dict[Any, Any], item).items()}
    if isinstance(item, list | tuple):
        return [plain(v) for v in cast(Any, item)]
    return item


def digest(item: Any) -> Any:
    if item is None or isinstance(item, str | int | float | bool):
        return item
    if not isinstance(item, BaseModel | dict | list | tuple | datetime):
        return type(item).__name__
    return hashlib.sha256(json.dumps(plain(item), sort_keys=True).encode()).hexdigest()


class Recorder:
    def __init__(self) -> None:
        self.depth = 0
        self.ids = 0
        self.ticks = 0
        self.timer = 0
        self.trace: list[Any] = []
        self.outputs: list[Any] = []

    def note(self, *items: Any) -> None:
        if self.depth:
            self.trace.append(list(items))


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

    patch.setattr(SystemClock, "now", now)
    patch.setattr(UuidIdGenerator, "new", new)
    patch.setattr(isolated_runner, "perf_counter_ns", timer)
    patch.setattr(behavior_context, "perf_counter_ns", timer)
    patch.setattr(behavior_models, "perf_counter_ns", timer)

    def wrap(owner: Any, name: str, *, handler: bool = False) -> None:
        original = getattr(owner, name)

        def begin(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
            if handler:
                rec.depth += 1
            rec.note(
                owner.__name__,
                name,
                [digest(x) for x in args],
                {k: digest(v) for k, v in kwargs.items()},
            )

        def end(result: Any = None, error: BaseException | None = None) -> None:
            if handler:
                if rec.depth == 1:
                    rec.outputs.append(
                        [
                            name,
                            plain(result)
                            if error is None
                            else {
                                "error": type(error).__name__,
                                "message": str(error),
                                "code": getattr(error, "code", None),
                                "data": plain(getattr(error, "data", None)),
                            },
                        ]
                    )
                rec.note(
                    "return" if error is None else "raise",
                    name,
                    digest(result) if error is None else type(error).__name__,
                )
                rec.depth -= 1

        if inspect.iscoroutinefunction(original):

            async def asynchronous(self: Any, *args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = await original(self, *args, **kwargs)
                except BaseException as exc:
                    end(error=exc)
                    raise
                end(result)
                return result

            patch.setattr(owner, name, asynchronous)
        else:

            def synchronous(self: Any, *args: Any, **kwargs: Any) -> Any:
                begin(args, kwargs)
                try:
                    result = original(self, *args, **kwargs)
                except BaseException as exc:
                    end(error=exc)
                    raise
                end(result)
                return result

            patch.setattr(owner, name, synchronous)

    for name in METHODS:
        wrap(ImprovementHandlers, name, handler=True)
    for owner, names in (
        (ControlRecordService, ("create", "stage")),
        (SqliteControlRecordStore, ("read", "list", "append")),
        (SqliteGovernanceStore, ("list_roles",)),
        (ImprovementThreadScope, ("require_thread", "require_record", "may_read")),
        (PairedEvaluationService, ("require_assessment", "require_current")),
        (
            ImprovementExposureService,
            ("prepare", "promotion_payload", "prepare_promotion", "apply_promotion"),
        ),
        (SqliteBehaviorExecutionStore, ("append", "promote")),
    ):
        for name in names:
            wrap(owner, name)

    original_auth = improvement_full.require_authenticated_authority

    def auth(*args: Any, **kwargs: Any) -> Any:
        rec.note("authority", [digest(x) for x in args])
        return original_auth(*args, **kwargs)

    patch.setattr(improvement_full, "require_authenticated_authority", auth)

    original_tx = SqliteLedger.transaction

    @contextmanager
    def transaction(self: SqliteLedger):
        rec.note("transaction", "enter")
        try:
            with original_tx(self) as result:
                yield result
        except BaseException:
            rec.note("transaction", "rollback")
            raise
        else:
            rec.note("transaction", "exit")

    patch.setattr(SqliteLedger, "transaction", transaction)


def controls(runtime: Any) -> ControlRecordService:
    return ControlRecordService(
        store=SqliteControlRecordStore(runtime.ledger.engine),
        clock=SystemClock(),
        ids=UuidIdGenerator(),
    )


async def draft_scenario(name: str, path: Path) -> None:
    runtime, project = await prepare_project(path)

    async def call(method: str, key: str, **kwargs: Any) -> Any:
        return await runtime.bus.dispatch(
            request("improvement/" + method, key, {"project_id": project, **kwargs})
        )

    try:
        body = proposal_body(project)
        if name == "plan_self":
            body["target_component"] = "EVALUATOR_CODE"
        proposed = value(
            await runtime.bus.dispatch(request("improvement/propose", "propose", body))
        )["improvement"]
        if name == "proposal_guards":
            assert (
                await runtime.bus.dispatch(request("improvement/propose", "overlap", body))
            ).error is None
            for target in ("PRODUCTION_MODEL_WEIGHTS", "UNKNOWN", "OFFICIAL_KPI"):
                denied = await runtime.bus.dispatch(
                    request(
                        "improvement/propose",
                        "deny-" + target,
                        {**body, "target_component": target},
                    )
                )
                assert denied.error is not None
            prior = controls(runtime).create(
                project_id=project,
                namespace="IMPROVEMENT",
                record_type="REVISION",
                state="PROPOSED",
                payload={
                    **proposed["payload"],
                    "candidate_lifecycle": "RETIRED",
                    "performance_verdict": "WORSE",
                },
            )
            assert prior.payload["performance_verdict"] == "WORSE"
            rejected = value(
                await runtime.bus.dispatch(
                    request(
                        "improvement/propose", "failed-digest", {**body, "scope_key": "fresh-scope"}
                    )
                )
            )["improvement"]
            assert rejected["state"] == "REJECTED_FAILED_DIGEST"
            denied = await call(
                "propose",
                "missing-thread",
                **{k: v for k, v in body.items() if k != "project_id"},
                thread_id="missing",
            )
            assert denied.error is not None
            return
        identifier = proposed["record_id"]
        if name == "draft_chain":
            for key, patch in (
                ("hypothesis", {"improvement_hypothesis": "revised"}),
                ("content", {"candidate_content": {"enabled": True}}),
            ):
                proposed = value(
                    await call(
                        "revise",
                        "revise-" + key,
                        improvement_revision_id=identifier,
                        expected_revision_digest=proposed["record_digest"],
                        patch=patch,
                        evidence_refs=[],
                        reason="golden",
                    )
                )["improvement"]
            for patch, expected in (({"forbidden": 1}, proposed["record_digest"]), ({}, "0" * 64)):
                assert (
                    await call(
                        "revise",
                        "reject-" + expected[:4],
                        improvement_revision_id=identifier,
                        expected_revision_digest=expected,
                        patch=patch,
                        evidence_refs=[],
                        reason="golden",
                    )
                ).error is not None
        plan = value(
            await call(
                "evaluation/plan",
                "plan",
                improvement_revision_id=identifier,
                baseline_digest="b" * 64
                if name == "plan_baseline"
                else proposed["payload"]["baseline_digest"],
                datasets=[{"contains_expected_values": name == "plan_hidden"}],
                evaluator_refs=[proposed["payload"]["candidate_digest"]]
                if name == "plan_self"
                else ["external"],
                guardrails=[{"critical": True, "status": "FAIL"}]
                if name == "assess_unresolved"
                else [],
                exposure_policy_ref="policy",
            )
        )["evaluation_plan"]
        held = name in {"plan_self", "plan_hidden", "plan_baseline"}
        assert plan["state"] == ("HELD" if held else "PLANNED")
        assessed = value(
            await call(
                "evaluation/assess",
                "assess",
                evaluation_plan_id=plan["record_id"],
                expected_evaluation_revision=plan["version"],
                result_refs=["opaque"],
                evaluator_result_refs=["missing"],
                exposure_ledger_ref="missing",
            )
        )
        assert assessed["evaluation_validity"] == ("INVALID" if held else "INCONCLUSIVE")
        plan = assessed["evaluation"]
        assert (
            await call(
                "evaluation/assess",
                "old-assess",
                evaluation_plan_id=plan["record_id"],
                expected_evaluation_revision=1,
                result_refs=[],
                evaluator_result_refs=[],
                exposure_ledger_ref="missing",
            )
        ).error is not None
        if name != "draft_chain":
            return
        for stage in ("OFFLINE", "SANDBOX", "SHADOW", "CANARY"):
            exposure = value(
                await call(
                    "exposure/prepare",
                    "exposure-" + stage,
                    improvement_revision_id=identifier,
                    evaluation_plan_id=plan["record_id"],
                    requested_exposure_state=stage,
                    scope={},
                    duration_budget={},
                    stop_rollback_contract={},
                )
            )["exposure"]
            read = (
                "shadow/read"
                if stage == "SHADOW"
                else "canary/read"
                if stage == "CANARY"
                else "exposure/read"
            )
            value(await call(read, "read-" + stage, record_id=exposure["record_id"]))
        promotion = value(
            await call(
                "promotion/prepare",
                "promotion",
                improvement_revision_id=identifier,
                assessment_refs=[plan["record_id"]],
                candidate_digest=proposed["payload"]["candidate_digest"],
                baseline_digest=proposed["payload"]["baseline_digest"],
                target_scope="99-v-draft",
                policy_version="v1",
            )
        )["promotion"]
        assert promotion["state"] == "NOT_ELIGIBLE"
        assert (
            await call(
                "promotion/decide",
                "deny-promotion",
                promotion_candidate_id=promotion["record_id"],
                decision="APPROVE",
                actor_ref="human:none",
                role_assignment_ref="none",
                approved_digest=promotion["record_digest"],
            )
        ).error is not None
        for index, refs in enumerate(([], ["guardrail"])):
            value(
                await call(
                    "rollback/prepare",
                    f"rollback-{index}",
                    promoted_revision_ref=identifier,
                    guardrail_failure_refs=refs,
                    rollback_target_digest="a" * 64,
                    target_scope="99-v-draft",
                )
            )
        value(
            await call(
                "retire/propose",
                "retire",
                improvement_revision_id=identifier,
                expected_revision_digest=proposed["record_digest"],
                reason="golden",
                evidence_refs=[],
            )
        )
        assert (
            await call(
                "retire/propose",
                "retire-stale",
                improvement_revision_id=identifier,
                expected_revision_digest="0" * 64,
                reason="golden",
                evidence_refs=[],
            )
        ).error is not None
        for method, extra in (
            ("read", {"improvement_revision_id": identifier}),
            ("list", {}),
            ("audit/read", {"improvement_revision_id": identifier}),
            ("evaluation/read", {"record_id": plan["record_id"]}),
            ("promotion/read", {"record_id": promotion["record_id"]}),
        ):
            value(await call(method, method, **extra))
    finally:
        runtime.close()


async def pair_scenario(name: str, path: Path) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": 1},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        path, {"op": "literal", "value": 0}, {"op": "literal", "value": 1}, cases
    ) as h:
        if name == "assess_critical":
            h.plan = (
                controls(h.runtime)
                .create(
                    project_id=h.project,
                    namespace="IMPROVEMENT",
                    record_type="EVALUATION_PLAN",
                    record_id=h.plan["record_id"],
                    state="PLANNED",
                    payload={
                        **h.plan["payload"],
                        "guardrails": [{"critical": True, "status": "FAIL"}],
                    },
                )
                .model_dump(mode="json")
            )
        measured = (await h.run())["pair"]["result"]
        result = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "assess-measured",
                    {
                        "project_id": h.project,
                        "evaluation_plan_id": h.plan["record_id"],
                        "expected_evaluation_revision": h.plan["version"],
                        "result_refs": [measured["pair_id"]],
                        "evaluator_result_refs": [measured["result_digest"]],
                        "exposure_ledger_ref": measured["exposure_id"],
                    },
                )
            )
        )
        assert result["evaluation_validity"] == "VALID"
        assert result["performance_verdict"] == ("WORSE" if name == "assess_critical" else "BETTER")
        assert result["promotion_eligibility"] is False


async def exposure_scenario(name: str, path: Path, patch: pytest.MonkeyPatch) -> None:
    async with exposure_harness(path, stage="SHADOW" if name == "expose_shadow" else "CANARY") as h:
        if name in {"expose_shadow", "exposure_error"}:
            if name == "exposure_error":

                def denied(*_args: Any, **_kwargs: Any) -> Any:
                    raise BehaviorPolicyError("BEHAVIOR_POLICY_CHANGED")

                patch.setattr(ImprovementExposureService, "prepare", denied)
                response = await h.call(
                    "improvement/exposure/prepare",
                    "prepare-error",
                    {
                        "improvement_revision_id": h.pair.proposal["record_id"],
                        "evaluation_plan_id": h.pair.plan["record_id"],
                        "requested_exposure_state": "CANARY",
                        "scope": {},
                        "duration_budget": {},
                        "stop_rollback_contract": {},
                    },
                )
                assert response.error is not None
            return
        promotion = await ready_promotion(h)
        if name == "promotion_guards":
            body = decision_body(h, promotion, "APPROVE")
            assert (
                await h.call(
                    "improvement/promotion/decide",
                    "wrong-digest",
                    {**body, "approved_digest": "0" * 64},
                )
            ).error is not None
            assert (
                await h.call(
                    "improvement/promotion/decide",
                    "wrong-role",
                    {**body, "role_assignment_ref": "none"},
                )
            ).error is not None
            return
        decision = "APPROVE" if name == "promote_approve" else "REJECT"
        result = value(
            await h.call(
                "improvement/promotion/decide", "decision", decision_body(h, promotion, decision)
            )
        )
        assert result["current_state_mutated"] is (decision == "APPROVE")


@pytest.mark.parametrize("name", SCENARIOS)
async def test_improvement_handler_outputs_and_effect_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    if name in {"assess_pair", "assess_critical"}:
        await pair_scenario(name, tmp_path)
    elif name.startswith(("expose_", "promote_", "promotion_", "exposure_")):
        await exposure_scenario(name, tmp_path, monkeypatch)
    else:
        await draft_scenario(name, tmp_path)
    observed = {"trace": rec.trace, "outputs": rec.outputs}
    assert rec.trace and rec.outputs
    # The notification consumer's actual input payload and emitted names are both frozen.
    notifications: list[Any] = []
    for method, result in rec.outputs:
        if method in {"evaluation_assess", "exposure_prepare"} and "error" not in result:
            rpc = (
                "improvement/evaluation/assess"
                if method == "evaluation_assess"
                else "improvement/exposure/prepare"
            )
            payload = result["evaluation" if method == "evaluation_assess" else "exposure"][
                "payload"
            ]
            key = (
                "critical_guardrail_failure"
                if method == "evaluation_assess"
                else "requested_exposure_state"
            )
            assert key in payload
            notifications.append([rpc, payload[key], notifications_for(rpc, result)])
    observed["notifications"] = plain(notifications)
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
