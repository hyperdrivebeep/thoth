"""Additional public persistence races and already-safe Evidence UoW regressions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import domain_snapshot, fail_after, request, value
from tests.integration.test_a03_storage_atomicity import (
    assert_reopened,
    create_link,
    creation,
    link_input,
    setup,
)

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.acquisition_trace import SqliteEvidenceUnitOfWork
from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.investigation import SqliteInvestigationStore
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.apps.runtime import AppRuntime
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.domain.enums import CutoffState
from thoth.domain.evidence import EvidenceSpan


async def portfolio_input(runtime: AppRuntime, ctx: dict[str, Any]) -> dict[str, Any]:
    ids: list[str] = []
    for index in range(2):
        created = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/create",
                    f"h-{index}",
                    {
                        **creation(ctx),
                        "statement": f"Alternative clock explanation {index}",
                    },
                )
            )
        )
        ids.append(created["hypothesis"]["hypothesis_id"])
    return {
        "project_id": ctx["project"],
        "object_id": ctx["object"],
        "portfolio_id": "portfolio:a03-storage",
        "hypothesis_ids": ids,
        "unknown_reserve": {"reason": "Unobserved cause remains possible"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["add_portfolio", "append_audit"])
async def test_public_portfolio_commit_rolls_back_projection_head_and_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    payload = await portfolio_input(runtime, ctx)
    baseline = domain_snapshot(runtime.ledger.engine)
    try:
        fail_after(monkeypatch, SqliteHypothesisStore, point)
        result = await runtime.bus.dispatch(
            request("hypothesis/portfolio/compose", "fault", payload)
        )
        assert result.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("remove", [False, True])
async def test_relation_write_rolls_back_with_portfolio_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remove: bool,
) -> None:
    runtime, ctx = await setup(tmp_path)
    payload = await portfolio_input(runtime, ctx)
    portfolio = value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/portfolio/compose",
                "portfolio",
                payload,
            )
        )
    )["portfolio"]
    method = "hypothesis/relation/add"
    command: dict[str, object] = {
        "project_id": ctx["project"],
        "portfolio_id": portfolio["portfolio_id"],
        "source_hypothesis_id": payload["hypothesis_ids"][0],
        "target_hypothesis_id": payload["hypothesis_ids"][1],
        "relation_type": "ALTERNATIVE_TO",
        "evidence_refs": ctx["spans"],
        "expected_portfolio_revision": portfolio["revision_digest"],
    }
    if remove:
        added = value(await runtime.bus.dispatch(request(method, "relation", command)))
        method = "hypothesis/relation/remove"
        command = {
            "project_id": ctx["project"],
            "relation_id": added["relation"]["relation_id"],
            "reason": "Relation invalidated by new evidence",
            "evidence_refs": ctx["spans"],
            "expected_portfolio_revision": added["portfolio"]["revision_digest"],
        }
    baseline = domain_snapshot(runtime.ledger.engine)
    try:
        fail_after(monkeypatch, SqliteHypothesisStore, "add_relation")
        result = await runtime.bus.dispatch(request(method, "fault", command))
        assert result.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_portfolio_revalidation_rolls_back_extra_audit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    payload = await portfolio_input(runtime, ctx)
    portfolio = value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/portfolio/compose",
                "portfolio",
                payload,
            )
        )
    )["portfolio"]
    baseline = domain_snapshot(runtime.ledger.engine)
    original = SqliteHypothesisStore.append_audit
    calls = 0

    def fault(store: SqliteHypothesisStore, record: Any) -> None:
        nonlocal calls
        original(store, record)
        calls += 1
        if calls == 2:
            raise RuntimeError("post-write trigger audit fault")

    try:
        monkeypatch.setattr(SqliteHypothesisStore, "append_audit", fault)
        result = await runtime.bus.dispatch(
            request(
                "hypothesis/portfolio/revalidate",
                "fault",
                {
                    "project_id": ctx["project"],
                    "portfolio_id": portfolio["portfolio_id"],
                    "trigger_reason": "new evidence",
                },
            )
        )
        assert result.error is not None
        assert calls == 2
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_stale_creation_object_guard_runs_inside_service_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    expected = runtime.ledger.read_heads(ctx["project"])[f"DECISION_OBJECT:{ctx['object']}"]
    baseline = domain_snapshot(runtime.ledger.engine)
    original = HypothesisService.create

    def race(service: HypothesisService, **kwargs: Any) -> Any:
        # The public handler passed its read guard; the bound ledger head has moved
        # before service admission. This injected read is limited to the service.
        original_heads = runtime.ledger.read_heads

        def advanced(project: str) -> dict[str, str]:
            return {
                **original_heads(project),
                f"DECISION_OBJECT:{ctx['object']}": "a" * 64,
            }

        with monkeypatch.context() as scoped:
            scoped.setattr(runtime.ledger, "read_heads", advanced)
            return original(service, **kwargs)

    try:
        monkeypatch.setattr(HypothesisService, "create", race)
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/create",
                "stale-object",
                {
                    **creation(ctx),
                    "expected_object_revision": expected,
                },
            )
        )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_tied_source_timestamp_read_returns_corrected_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fixed_clock(clock: SystemClock) -> datetime:
        return datetime(2026, 9, 1, tzinfo=UTC)

    monkeypatch.setattr(SystemClock, "now", fixed_clock)
    runtime, ctx = await setup(tmp_path)
    try:
        corrected = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/metadata/correct",
                    "metadata",
                    {
                        "project_id": ctx["project"],
                        "source_id": ctx["source"],
                        "rights": "REUSE",
                    },
                )
            )
        )["source"]
        read = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/read",
                    "read-source",
                    {
                        "project_id": ctx["project"],
                        "span_id": ctx["spans"][0],
                    },
                )
            )
        )
        assert read["source"]["source_id"] == corrected["source_id"]
        baseline = domain_snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["after_observation", "after_claim_candidate", "after_audit"])
async def test_existing_evidence_uow_stays_atomic_through_public_link_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    link = await create_link(runtime, ctx)
    baseline = domain_snapshot(runtime.ledger.engine)

    def fault(uow: SqliteEvidenceUnitOfWork, observed: str) -> None:
        if observed == step:
            raise RuntimeError("existing acquisition boundary fault")

    try:
        monkeypatch.setattr(SqliteEvidenceUnitOfWork, "_fault", fault)
        result = await runtime.bus.dispatch(
            request(
                "evidence/link/correct",
                "fault",
                {
                    **link_input(ctx),
                    "evidence_id": link["evidence_id"],
                    "observed_statement": "Corrected observation",
                },
            )
        )
        assert result.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_held_prohibited_evidence_revalidation_remains_non_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    link = await create_link(runtime, ctx)
    original = SqliteArtifactLedger.read_evidence

    def held(store: SqliteArtifactLedger, span_id: str) -> EvidenceSpan | None:
        span = original(store, span_id)
        return (
            None
            if span is None
            else span.model_copy(update={"cutoff_state": CutoffState.PROHIBITED_CONTEXT})
        )

    try:
        monkeypatch.setattr(SqliteArtifactLedger, "read_evidence", held)
        result = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/revalidate",
                    "hold",
                    {
                        "project_id": ctx["project"],
                        "evidence_id": link["evidence_id"],
                    },
                )
            )
        )["evidence"]
        assert result["cutoff_eligibility"] == "PROHIBITED_CONTEXT"
        assert result["support_status"] == "UNRESOLVED"
        audit = SqliteEvidenceGraphStore(runtime.ledger.engine).list_audit(
            ctx["project"],
            result["evidence_id"],
            offset=0,
            limit=100,
        )
        assert len(audit) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner, point",
    [
        (SqliteInvestigationStore, "create"),
        (SqliteHypothesisStore, "append_audit"),
    ],
)
async def test_counterevidence_request_rolls_back_investigation_and_hypothesis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: type[object],
    point: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    current = value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/create",
                "hypothesis",
                creation(ctx),
            )
        )
    )["hypothesis"]
    baseline = domain_snapshot(runtime.ledger.engine)
    try:
        fail_after(monkeypatch, owner, point)
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/counterevidence/request",
                "fault",
                {
                    "project_id": ctx["project"],
                    "hypothesis_id": current["hypothesis_id"],
                    "source_scope": ["independent sources"],
                },
            )
        )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["hypothesis/create", "hypothesis/portfolio/compose"])
async def test_current_object_head_cannot_admit_stale_object_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    payload = (
        creation(ctx) if method == "hypothesis/create" else await portfolio_input(runtime, ctx)
    )
    store = SqliteDecisionObjectStore(runtime.ledger.engine)
    old = store.read_object(ctx["project"], ctx["object"], None)
    assert old is not None
    try:
        updated = value(
            await runtime.bus.dispatch(
                request(
                    "object/frame/revise",
                    "new-frame",
                    {
                        "project_id": ctx["project"],
                        "object_id": ctx["object"],
                        "expected_revision_digest": old.revision_digest,
                        "frame_patch": {
                            "problem_frame": "Why did the revised clock measurement drift?"
                        },
                        "evidence_refs": ctx["spans"],
                        "reason": "new observed problem",
                    },
                )
            )
        )["object"]
        current_digest = updated["revision_digest"]
        assert current_digest != old.revision_digest
        assert (
            runtime.ledger.read_heads(ctx["project"])[f"DECISION_OBJECT:{ctx['object']}"]
            == current_digest
        )
        baseline = domain_snapshot(runtime.ledger.engine)
        original = SqliteDecisionObjectStore.read_object

        def stale_latest(
            store: SqliteDecisionObjectStore,
            project_id: str,
            object_id: str,
            revision_digest: str | None,
        ) -> DecisionObjectRecord | None:
            if (project_id, object_id, revision_digest) == (
                ctx["project"],
                ctx["object"],
                None,
            ):
                return old
            return original(store, project_id, object_id, revision_digest)

        with monkeypatch.context() as scoped:
            scoped.setattr(SqliteDecisionObjectStore, "read_object", stale_latest)
            response = await runtime.bus.dispatch(
                request(
                    method,
                    "stale-projection",
                    {
                        **payload,
                        "expected_object_revision": current_digest,
                    },
                )
            )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)
