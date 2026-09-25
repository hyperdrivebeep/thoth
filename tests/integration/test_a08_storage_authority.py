from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

import httpx
import pytest
from sqlalchemy import event
from tests.integration.source_bound_problem import publish_fixture_problem
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    fail_after,
    prepare_project,
    request,
    value,
)
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery

from thoth.adapters.auth import SecureSessionTokenIssuer, StaticCredentialVerifier
from thoth.adapters.http.app import create_app
from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.auth import SqliteAuthSessionStore
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.transaction import _AMBIENT  # pyright: ignore[reportPrivateUsage]
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.local_auth import LocalAuthenticationService
from thoth.apps.runtime import create_runtime
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.improvement import ImprovementEvaluation, ImprovementEvaluationRequest
from thoth.domain.sandbox import SandboxExecutionState
from thoth.protocol.jsonrpc import JsonRpcResponse


@pytest.mark.parametrize("change_context", [False, True])
async def test_normal_evaluation_has_durable_request_and_no_open_write_transaction(
    tmp_path: Path,
    change_context: bool,
) -> None:
    observed: list[str] = []
    open_transactions: list[bool] = []

    class ProbeEvaluator(DeterministicIndependentImprovementEvaluator):
        def evaluate(
            self,
            *,
            baseline_digest: str,
            candidate_digest: str,
            fixture_digest: str,
            hidden_holdout_digest: str,
            max_requests: int,
            timeout_seconds: int,
        ) -> ImprovementEvaluation:
            open_transactions.append(_AMBIENT.get() is not None)
            assert _AMBIENT.get() is None, "evaluator I/O is inside a SQLite write transaction"
            requests = SqliteControlRecordStore(runtime.ledger.engine).list(
                project,
                "IMPROVEMENT_RUNTIME",
                "EVALUATION_REQUEST",
            )
            assert len(requests) == 1 and requests[0].state == "EVALUATING"
            assert requests[0].payload["candidate_digest"] == candidate_digest
            observed.append(requests[0].record_id)

            async def peer_command() -> None:
                peer = create_runtime(workspace)
                try:
                    current = value(
                        await peer.bus.dispatch(
                            request(
                                "project/read",
                                "peer-read",
                                {
                                    "project_id": project,
                                },
                            )
                        )
                    )
                    if change_context:
                        value(
                            await peer.bus.dispatch(
                                request(
                                    "project/metadata/update",
                                    "peer-update",
                                    {
                                        "project_id": project,
                                        "expected_revision": current["revision"],
                                        "name": "Peer updated evaluation basis",
                                    },
                                )
                            )
                        )
                finally:
                    peer.close()

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(peer_command())).result(timeout=10)
            return super().evaluate(
                baseline_digest=baseline_digest,
                candidate_digest=candidate_digest,
                fixture_digest=fixture_digest,
                hidden_holdout_digest=hidden_holdout_digest,
                max_requests=max_requests,
                timeout_seconds=timeout_seconds,
            )

    scenario = "a08-context-change" if change_context else "a08-outside-transaction"
    runtime, project, thread = await prepare_recovery(
        tmp_path,
        scenario,
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC repeated evaluation fixture"),
                (SandboxExecutionState.FAILED, "SEMANTIC repeated evaluation fixture"),
            )
        ),
        max_retries=0,
        improvement_evaluator=ProbeEvaluator(),
    )
    workspace = tmp_path / scenario
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "first",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "second",
                {
                    "project_id": project,
                    "thread_id": thread,
                },
            )
        )
        assert open_transactions == [False], (
            "normal evaluator entered while write transaction was open"
        )
        second = value(response)
        result = second["recursive_improvement"]
        assert len(observed) == 1
        assert result["state"] == ("HELD" if change_context else "PROMOTED_LOCAL")
        records = SqliteControlRecordStore(runtime.ledger.engine)
        final_request = records.read(project, "IMPROVEMENT_RUNTIME", observed[0])
        assert final_request is not None
        assert final_request.state == ("HELD" if change_context else "FINALIZED")
        if change_context:
            assert result["rollback_reason"] == "CONTEXT_CHANGED"
            assert (
                SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
                    project,
                    BehaviorArtifactKind.WORKFLOW_DEFINITION,
                )
                is None
            )
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        stored = SqliteControlRecordStore(reopened.ledger.engine).read(
            project,
            "IMPROVEMENT_RUNTIME",
            observed[0],
        )
        assert stored is not None and stored.state == ("HELD" if change_context else "FINALIZED")
    finally:
        reopened.close()


async def prepare_public_evaluation(workspace: Path):
    runtime, project = await prepare_project(workspace)
    proposed = value(
        await runtime.bus.dispatch(
            request(
                "improvement/propose",
                "propose",
                {
                    "project_id": project,
                    "target_component": "PROMPT",
                    "scope_key": "local:research",
                    "trigger_refs": [],
                    "baseline_digest": "a" * 64,
                    "candidate_content": {"instruction": "request source-grounded counterevidence"},
                    "improvement_hypothesis": "Increase counterevidence coverage",
                    "evaluation_contract_ref": "evaluation:local",
                },
            )
        )
    )["improvement"]
    planned = value(
        await runtime.bus.dispatch(
            request(
                "improvement/evaluation/plan",
                "plan",
                {
                    "project_id": project,
                    "improvement_revision_id": proposed["record_id"],
                    "baseline_digest": "a" * 64,
                    "datasets": [],
                    "evaluator_refs": ["eval:unverified"],
                    "guardrails": [],
                    "exposure_policy_ref": "local:bounded",
                },
            )
        )
    )["evaluation_plan"]
    return runtime, project, proposed, planned


async def test_public_opaque_result_references_cannot_certify_improvement(tmp_path: Path) -> None:
    runtime, project, proposed, planned = await prepare_public_evaluation(tmp_path / "workspace")
    try:
        assessed: dict[str, Any] = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "assess",
                    {
                        "project_id": project,
                        "evaluation_plan_id": planned["record_id"],
                        "result_refs": ["invented:baseline", "invented:candidate"],
                        "evaluator_result_refs": ["invented:evaluator-result"],
                        "exposure_ledger_ref": "invented:exposure",
                        "expected_evaluation_revision": planned["version"],
                    },
                )
            )
        )
        assert assessed["evaluation_validity"] == "INCONCLUSIVE"
        assert assessed["performance_verdict"] == "INCONCLUSIVE"
        assert assessed["promotion_eligibility"] is False
        promotion = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/promotion/prepare",
                    "promotion",
                    {
                        "project_id": project,
                        "improvement_revision_id": proposed["record_id"],
                        "assessment_refs": [planned["record_id"]],
                        "candidate_digest": proposed["payload"]["candidate_digest"],
                        "baseline_digest": "a" * 64,
                        "target_scope": "local:research",
                        "policy_version": "local:bounded",
                    },
                )
            )
        )
        assert promotion["eligibility"] == "NOT_ELIGIBLE"
    finally:
        runtime.close()


@pytest.mark.parametrize("fault_point", ["registry", "exposure"])
async def test_finalization_fault_preserves_preparation_and_rolls_back_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_point: str,
) -> None:
    scenario = "a08-finalization-" + fault_point
    workspace = tmp_path / scenario
    runtime, project, thread = await prepare_recovery(
        tmp_path,
        scenario,
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC finalize fault"),
                (SandboxExecutionState.FAILED, "SEMANTIC finalize fault"),
            )
        ),
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "first",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        with monkeypatch.context() as patch:
            if fault_point == "registry":
                fail_after(patch, SqliteBehaviorArtifactStore, "activate")
            else:
                original = ControlRecordService.create

                def create_then_fail(
                    service: ControlRecordService, **arguments: Any
                ) -> ControlRecord:
                    record = original(service, **arguments)
                    if arguments.get("record_type") == "EXPOSURE":
                        raise RuntimeError("injected post-exposure write failure")
                    return record

                patch.setattr(ControlRecordService, "create", create_then_fail)
            response = await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "fault",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
            assert response.error is not None
        records = SqliteControlRecordStore(runtime.ledger.engine)
        requests = records.list(project, "IMPROVEMENT_RUNTIME", "EVALUATION_REQUEST")
        assert len(requests) == 1 and requests[0].state == "FAILED"
        assert records.list(project, "IMPROVEMENT_RUNTIME", "EXPOSURE") == ()
        assert records.list(project, "IMPROVEMENT_RUNTIME", "RUN") == ()
        assert (
            SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
                project,
                BehaviorArtifactKind.WORKFLOW_DEFINITION,
            )
            is None
        )
        after = domain_snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == after
    finally:
        reopened.close()


@pytest.mark.parametrize("authority_case", ["spoofed", "revoked", "unbound", "reject"])
async def test_legacy_pending_promotion_cannot_bypass_current_authority(
    tmp_path: Path,
    authority_case: str,
) -> None:
    runtime, project, proposed, _ = await prepare_public_evaluation(tmp_path / "workspace")
    try:
        role = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "owner",
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "actor_id": "human:owner",
                        "role": "improvement-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["IMPROVEMENT_OWNER"],
                    },
                )
            )
        )["role"]
        # A pre-fix PENDING row is a compatibility input, not evaluation proof.
        controls = ControlRecordService(
            store=SqliteControlRecordStore(runtime.ledger.engine),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        pending = controls.create(
            project_id=project,
            namespace="IMPROVEMENT",
            record_type="PROMOTION",
            state="PENDING",
            payload={"improvement_revision_id": proposed["record_id"]},
        )
        context = nullcontext()
        if authority_case == "spoofed":
            context = authenticated_actor_scope(
                AuthenticatedActorContext(
                    actor_id="human:other",
                    session_id="session:other",
                    project_id=project,
                    role_assignment_id="role:other",
                    role="revision-writer",
                    capabilities=("WRITE",),
                    data_scopes=("PROJECT",),
                )
            )
        elif authority_case == "revoked":
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/role/revoke",
                        "revoke",
                        {
                            "project_id": project,
                            "expected_revision": 1,
                            "role_assignment_id": role["role_assignment_id"],
                        },
                    )
                )
            )
        before = domain_snapshot(runtime.ledger.engine)
        with context:
            denied = await runtime.bus.dispatch(
                request(
                    "improvement/promotion/decide",
                    "decide",
                    {
                        "project_id": project,
                        "promotion_candidate_id": pending.record_id,
                        "decision": "REJECT" if authority_case == "reject" else "APPROVE",
                        "actor_ref": "human:owner",
                        "role_assignment_ref": role["role_assignment_id"],
                        "approved_digest": pending.record_digest,
                    },
                )
            )
        if authority_case == "reject":
            rejected = value(denied)
            assert rejected["promotion"]["state"] == "REJECTED"
            assert rejected["current_state_mutated"] is False
            return
        assert denied.error is not None
        if authority_case == "spoofed":
            assert denied.error.code == -32040
        elif authority_case == "revoked":
            assert denied.error.message == "authority role missing"
        else:
            assert denied.error.message == "promotion assessment basis changed"
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_wrong_baseline_result_cannot_activate_candidate_even_with_valid_hash(
    tmp_path: Path,
) -> None:
    class WrongBaselineEvaluator(DeterministicIndependentImprovementEvaluator):
        def evaluate(self, **values: Any) -> ImprovementEvaluation:
            draft = (
                super().evaluate(**values).model_dump(mode="python", exclude={"evaluation_digest"})
            )
            draft["baseline_digest"] = "9" * 64
            return ImprovementEvaluation.model_validate(
                {
                    **draft,
                    "evaluation_digest": domain_digest(
                        "IMPROVEMENT_EVALUATION",
                        "1.0.0",
                        canonical_payload(draft),
                    ),
                }
            )

    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "a08-wrong-baseline",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC binding check"),
                (SandboxExecutionState.FAILED, "SEMANTIC binding check"),
            )
        ),
        max_retries=0,
        improvement_evaluator=WrongBaselineEvaluator(),
    )
    try:
        for ordinal in (1, 2):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        str(ordinal),
                        {
                            "project_id": project,
                            "thread_id": thread,
                        },
                    )
                )
            )
        improvement = result["recursive_improvement"]
        assert improvement["state"] == "ROLLED_BACK"
        assert improvement["rollback_reason"] == "EVALUATION_BINDING_MISMATCH"
        assert improvement["active_digest"] == improvement["baseline_digest"]
    finally:
        runtime.close()


async def test_repeated_successful_candidate_keeps_the_actual_active_registry(
    tmp_path: Path,
) -> None:
    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "a08-successful-repeat",
        RecoverySandboxAdapter(
            tuple(
                (SandboxExecutionState.FAILED, "SEMANTIC repeated active candidate")
                for _ in range(3)
            )
        ),
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(),
    )
    try:
        for ordinal in (1, 2):
            value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        str(ordinal),
                        {
                            "project_id": project,
                            "thread_id": thread,
                        },
                    )
                )
            )
        registry = SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
            project,
            BehaviorArtifactKind.WORKFLOW_DEFINITION,
        )
        assert registry is not None
        third = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "3",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )["recursive_improvement"]
        assert third["state"] == "REJECTED_SAME_DIGEST"
        assert third["active_digest"] == registry.active_digest
        assert (
            SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
                project,
                BehaviorArtifactKind.WORKFLOW_DEFINITION,
            )
            == registry
        )
        assert (
            len(
                SqliteControlRecordStore(runtime.ledger.engine).list(
                    project,
                    "IMPROVEMENT_RUNTIME",
                    "EVALUATION_REQUEST",
                )
            )
            == 1
        )
    finally:
        runtime.close()


async def test_late_evaluator_result_cannot_activate_after_request_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LateEvaluator(DeterministicIndependentImprovementEvaluator):
        def evaluate(self, **values: Any) -> ImprovementEvaluation:
            request_record = SqliteControlRecordStore(runtime.ledger.engine).list(
                project,
                "IMPROVEMENT_RUNTIME",
                "EVALUATION_REQUEST",
            )[0]
            expired = request_record.created_at + timedelta(seconds=61)

            def expired_now(clock: SystemClock) -> datetime:
                del clock
                return expired

            monkeypatch.setattr(SystemClock, "now", expired_now)
            return super().evaluate(**values)

    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "a08-late-evaluation",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC deadline test"),
                (SandboxExecutionState.FAILED, "SEMANTIC deadline test"),
            )
        ),
        max_retries=0,
        improvement_evaluator=LateEvaluator(),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "first",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )["recursive_improvement"]
        assert result["state"] == "ROLLED_BACK"
        assert result["rollback_reason"] == "TIMEOUT"
        assert result["active_digest"] == result["baseline_digest"]
    finally:
        runtime.close()


@pytest.mark.parametrize("expire_session", [False, True])
async def test_http_session_expiry_during_evaluation_holds_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expire_session: bool,
) -> None:
    class ExpiringEvaluator(DeterministicIndependentImprovementEvaluator):
        def evaluate(self, **values: Any) -> ImprovementEvaluation:
            if not expire_session:
                return super().evaluate(**values)
            expired = issued.session.expires_at + timedelta(seconds=1)

            def expired_now(clock: SystemClock) -> datetime:
                del clock
                return expired

            monkeypatch.setattr(SystemClock, "now", expired_now)
            return super().evaluate(**values)

    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "a08-session-expiry",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC session bound evaluation"),
                (SandboxExecutionState.FAILED, "SEMANTIC session bound evaluation"),
            )
        ),
        max_retries=0,
        improvement_evaluator=ExpiringEvaluator(),
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "first",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        current = value(
            await runtime.bus.dispatch(
                request(
                    "project/read",
                    "read-project",
                    {
                        "project_id": project,
                    },
                )
            )
        )
        await publish_fixture_problem(runtime, project)
        role = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "session-role",
                    {
                        "project_id": project,
                        "expected_revision": current["revision"],
                        "actor_id": "human:evaluation-owner",
                        "role": "project-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["CAP_READ", "CAP_WRITE", "CAP_THREAD", "CAP_REVISION"],
                    },
                )
            )
        )["role"]
        auth = LocalAuthenticationService(
            sessions=SqliteAuthSessionStore(runtime.ledger.engine),
            governance=SqliteGovernanceStore(runtime.ledger.engine),
            credentials=StaticCredentialVerifier(
                {"human:evaluation-owner": hashlib.sha256(b"local-fixture").hexdigest()}
            ),
            tokens=SecureSessionTokenIssuer(),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            lifetime=timedelta(seconds=30),
        )
        issued = await auth.issue_http_session(
            actor_id="human:evaluation-owner",
            credential="local-fixture",
            project_id=project,
            role_assignment_id=role["role_assignment_id"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus, auth=auth)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/rpc",
                json=request(
                    "thread/input",
                    "second",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                ).model_dump(mode="json", by_alias=True),
                headers={"Authorization": "Bearer " + issued.bearer_token},
            )
        assert response.status_code == 200, response.json().get("error")
        reply = JsonRpcResponse.model_validate(response.json())
        if expire_session:
            assert reply.error is not None
            assert reply.error.data == {"reason_code": "RESOURCE_AUTHORITY_CHANGED"}
            # Expired sessions cannot receive source-bound results. Inspect the
            # persisted local owner record separately to verify the held transition.
            held = [
                record
                for record in SqliteControlRecordStore(runtime.ledger.engine).list(
                    project, "IMPROVEMENT_RUNTIME", "RUN"
                )
                if record.state == "HELD"
            ]
            assert len(held) == 1
            result = held[0].payload
        else:
            result = value(reply)["recursive_improvement"]
        assert result["state"] == ("HELD" if expire_session else "PROMOTED_LOCAL")
        registry = SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
            project,
            BehaviorArtifactKind.WORKFLOW_DEFINITION,
        )
        if expire_session:
            assert result["rollback_reason"] == "CONTEXT_CHANGED"
            assert registry is None
        else:
            assert registry is not None and registry.active_digest == result["candidate_digest"]
    finally:
        runtime.close()


async def test_new_failed_candidate_restores_pre_evaluation_active_behavior(tmp_path: Path) -> None:
    class SecondFails(DeterministicIndependentImprovementEvaluator):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def evaluate(self, **values: Any) -> ImprovementEvaluation:
            self.calls += 1
            evaluation = super().evaluate(**values)
            if self.calls == 1:
                return evaluation
            draft = evaluation.model_dump(mode="python", exclude={"evaluation_digest"})
            draft["critical_regression"] = True
            return ImprovementEvaluation.model_validate(
                {
                    **draft,
                    "evaluation_digest": domain_digest(
                        "IMPROVEMENT_EVALUATION",
                        "1.0.0",
                        canonical_payload(draft),
                    ),
                }
            )

    evaluator = SecondFails()
    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "a08-preserve-active",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC first variant"),
                (SandboxExecutionState.FAILED, "SEMANTIC first variant"),
                (SandboxExecutionState.FAILED, "SEMANTIC second variant"),
                (SandboxExecutionState.FAILED, "SEMANTIC second variant"),
            )
        ),
        max_retries=0,
        improvement_evaluator=evaluator,
    )
    try:
        for ordinal in (1, 2):
            value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        str(ordinal),
                        {
                            "project_id": project,
                            "thread_id": thread,
                        },
                    )
                )
            )
        store = SqliteBehaviorArtifactStore(runtime.ledger.engine)
        before = store.read_registry(project, BehaviorArtifactKind.WORKFLOW_DEFINITION)
        assert before is not None
        result: dict[str, Any] = {}
        for ordinal in (3, 4):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        str(ordinal),
                        {
                            "project_id": project,
                            "thread_id": thread,
                        },
                    )
                )
            )["recursive_improvement"]
        assert evaluator.calls == 2 and result["state"] == "ROLLED_BACK"
        after = store.read_registry(project, BehaviorArtifactKind.WORKFLOW_DEFINITION)
        assert after is not None and after.active_digest == before.active_digest
        candidate = store.read(project, result["behavior_artifact_id"])
        assert candidate is not None and candidate.parent_digest == before.active_digest
    finally:
        runtime.close()


async def test_deadline_includes_wait_for_actual_sqlite_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock_value = [datetime.now(UTC)]
    locked, release = Event(), Event()
    armed = False
    crossed: list[bool] = []
    peer_futures: list[Future[None]] = []
    pool = ThreadPoolExecutor(max_workers=1)

    def logical_now(clock: SystemClock) -> datetime:
        del clock
        return clock_value[0]

    monkeypatch.setattr(SystemClock, "now", logical_now)

    def peer_writer() -> None:
        with runtime.ledger.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            locked.set()
            assert release.wait(timeout=10)
            connection.commit()

    def release_at_acquisition(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        nonlocal armed
        del connection, cursor, parameters, context, many
        if armed and statement == "BEGIN IMMEDIATE":
            armed = False
            clock_value[0] += timedelta(seconds=2)
            crossed.append(True)
            release.set()

    class LockedEvaluator(DeterministicIndependentImprovementEvaluator):
        def evaluate(self, **values: Any) -> ImprovementEvaluation:
            nonlocal armed
            stored = SqliteControlRecordStore(runtime.ledger.engine).list(
                project,
                "IMPROVEMENT_RUNTIME",
                "EVALUATION_REQUEST",
            )[0]
            prepared = ImprovementEvaluationRequest.model_validate(stored.payload)
            clock_value[0] = prepared.created_at + timedelta(seconds=59)
            peer_futures.append(pool.submit(peer_writer))
            assert locked.wait(timeout=5)
            armed = True
            return super().evaluate(**values)

    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "a08-lock-deadline",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC lock deadline"),
                (SandboxExecutionState.FAILED, "SEMANTIC lock deadline"),
            )
        ),
        max_retries=0,
        improvement_evaluator=LockedEvaluator(),
    )
    event.listen(runtime.ledger.engine, "before_cursor_execute", release_at_acquisition)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "first",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )["recursive_improvement"]
        for future in peer_futures:
            future.result(timeout=5)
        assert crossed == [True]
        assert result["state"] == "ROLLED_BACK"
        assert result["rollback_reason"] == "TIMEOUT"
    finally:
        release.set()
        pool.shutdown(wait=True)
        event.remove(runtime.ledger.engine, "before_cursor_execute", release_at_acquisition)
        runtime.close()
