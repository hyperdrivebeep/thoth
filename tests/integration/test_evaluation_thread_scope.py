"""A runnable plan must not be attached to another actor's private Thread."""

from pathlib import Path

import pytest
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.resource_scope_helpers import denial, scope_harness, value
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.domain.resource_scope import ResourceScopeError


async def test_peer_cannot_attach_improvement_to_another_workstream_thread(tmp_path: Path) -> None:
    async with scope_harness(tmp_path, evaluation_catalog=FrozenEvaluationCatalog(())) as h:
        thread_id = "thread:alpha-improvement"
        value(
            await h.call(
                "alpha",
                "thread/start",
                "alpha-thread",
                {
                    "thread_id": thread_id,
                    "problem": "Alpha private work",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        proposal: dict[str, object] = {
            "thread_id": thread_id,
            "target_component": "WORKFLOW_GATE_ORDER",
            "scope_key": "alpha:offline",
            "trigger_refs": [],
            "baseline_digest": "a" * 64,
            "candidate_content": {"kind": "WORKFLOW_DEFINITION", "version": "1"},
            "improvement_hypothesis": "Bounded local comparison",
            "evaluation_contract_ref": "local:1",
        }
        own = value(await h.call("alpha", "improvement/propose", "own-proposal", proposal))
        assert own["improvement"]["state"] == "PROPOSED"
        improvement_id = own["improvement"]["record_id"]
        plan = value(
            await h.call(
                "alpha",
                "improvement/evaluation/plan",
                "own-plan",
                {
                    "improvement_revision_id": improvement_id,
                    "baseline_digest": "a" * 64,
                    "datasets": [],
                    "evaluator_refs": [],
                    "guardrails": [],
                    "exposure_policy_ref": "offline",
                },
            )
        )["evaluation_plan"]
        before = SqliteControlRecordStore(h.runtime.ledger.engine).list(h.project, "IMPROVEMENT")
        denied = await h.call(
            "beta",
            "improvement/propose",
            "foreign-thread-proposal",
            {
                **proposal,
                "scope_key": "beta:offline",
            },
        )
        denial(denied, "AUTH_DATA_SCOPE_DENIED")
        denial(
            await h.call(
                "alpha",
                "improvement/propose",
                "missing-thread-proposal",
                {
                    **proposal,
                    "thread_id": "thread:missing",
                    "scope_key": "missing:offline",
                },
            ),
            "EVALUATION_THREAD_NOT_FOUND",
        )
        denied_calls: tuple[tuple[str, dict[str, object]], ...] = (
            ("improvement/read", {"improvement_revision_id": improvement_id}),
            ("improvement/audit/read", {"improvement_revision_id": improvement_id}),
            ("improvement/evaluation/read", {"record_id": plan["record_id"]}),
            (
                "improvement/evaluation/run",
                {
                    "evaluation_plan_id": plan["record_id"],
                    "expected_evaluation_revision": 1,
                    "binding_id": "local:1",
                },
            ),
            (
                "improvement/evaluation/plan",
                {
                    "improvement_revision_id": improvement_id,
                    "baseline_digest": "a" * 64,
                    "datasets": [],
                    "evaluator_refs": [],
                    "guardrails": [],
                    "exposure_policy_ref": "offline",
                },
            ),
        )
        for method, payload in denied_calls:
            denial(
                await h.call("beta", method, "denied-" + method, payload), "AUTH_DATA_SCOPE_DENIED"
            )
        assert (
            value(await h.call("beta", "improvement/list", "filtered-list", {}))["improvements"]
            == []
        )
        assert (
            value(
                await h.call(
                    "alpha",
                    "improvement/read",
                    "own-read",
                    {
                        "improvement_revision_id": improvement_id,
                    },
                )
            )["improvement"]["record_id"]
            == improvement_id
        )
        assert (
            SqliteControlRecordStore(h.runtime.ledger.engine).list(h.project, "IMPROVEMENT")
            == before
        )


async def test_pair_read_rechecks_bound_thread_before_output_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "one", "payload": {}},
            "expected_output": {"answer": 1},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path,
        {"op": "literal", "value": 0},
        {"op": "literal", "value": 1},
        cases,
        thread_id="thread:result-scope",
    ) as h:
        completed = await h.run()
        assert completed["state"] == "COMPLETE", completed

        def deny_thread(_scope: ImprovementThreadScope, _project: str, thread: object) -> None:
            if thread is None:
                return
            assert thread == "thread:result-scope"
            raise ResourceScopeError("AUTH_DATA_SCOPE_DENIED")

        def no_read(_objects: ContentAddressedObjectStore, _digest: str) -> bytes:
            raise AssertionError("Thread denial must precede output I/O")

        monkeypatch.setattr(ImprovementThreadScope, "require_thread", deny_thread)
        monkeypatch.setattr(ContentAddressedObjectStore, "read", no_read)
        denied = await h.runtime.bus.dispatch(
            request(
                "improvement/evaluation/result/read",
                "denied-read",
                {
                    "project_id": h.project,
                    "pair_id": completed["pair"]["spec"]["pair_id"],
                },
            )
        )
        assert denied.error is not None and denied.error.data is not None
        assert denied.error.data["reason_code"] == "AUTH_DATA_SCOPE_DENIED"
