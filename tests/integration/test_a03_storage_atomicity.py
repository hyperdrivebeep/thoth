"""Public upstream regressions; imports exist in the frozen pre-fix source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.research_measurement_helpers import connect_local_measurement_contract
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    fail_after,
    prepare_project,
    request,
    value,
)

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence_graph import EvidenceAuditRecord
from thoth.domain.hypothesis_full import HypothesisAuditRecord, HypothesisRecord
from thoth.protocol.jsonrpc import RpcErrorCode


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _integer(value: object) -> int:
    assert type(value) is int
    return value


async def _confirm_setup_source_time(runtime: AppRuntime, project_id: str) -> str:
    before = _record(
        value(
            await runtime.bus.query(
                request("project/source/list", "time-basis-before", {"project_id": project_id})
            )
        )
    )
    artifacts_before = [_record(item) for item in _list(before["artifacts"])]
    assessments_before = [_record(item) for item in _list(before["source_times"])]
    bindings_before = [_record(item) for item in _list(before["bindings"])]
    assert len(artifacts_before) == len(assessments_before) == len(bindings_before) == 1
    artifact = artifacts_before[0]
    assessment = assessments_before[0]
    source_before = SqliteEvidenceGraphStore(runtime.ledger.engine).read_source_by_artifact(
        _text(artifact["artifact_id"])
    )
    assert source_before is not None
    cutoff = _record(before["cutoff_basis"])
    assert artifact["cutoff_state"] == assessment["cutoff_state"] == "UNKNOWN_TIME"
    assert assessment["reason_code"] == "NO_DOCUMENT_DATE"
    assert bindings_before[0]["state"] == "ACTIVE"
    evidence_before = _record(
        value(
            await runtime.bus.query(
                request("evidence/list", "time-evidence-before", {"project_id": project_id})
            )
        )
    )
    spans_before = [_record(item) for item in _list(evidence_before["spans"])]
    assert spans_before and all(item["cutoff_state"] == "UNKNOWN_TIME" for item in spans_before)
    assert all(item["authority_state"] == "OFFICIAL" for item in spans_before)

    basis: dict[str, object] = {
        "project_id": project_id,
        "artifact_id": _text(assessment["artifact_id"]),
        "source_version_id": _text(assessment["source_version_id"]),
        "byte_sha256": _text(assessment["byte_sha256"]),
        "expected_project_revision": _integer(cutoff["project_revision"]),
        "expected_cutoff_at": _text(cutoff["cutoff_at"]),
        "expected_assessment_revision": _integer(assessment["revision"]),
        "expected_metadata_digest": _text(assessment["metadata_digest"]),
        "assertion": "ON_OR_BEFORE_CUTOFF",
    }
    confirmed = _record(
        value(
            await runtime.bus.dispatch(
                request("project/source/time/confirm", "time-confirm-before-a03", basis)
            )
        )
    )
    assert _record(confirmed["source_time"])["cutoff_state"] == "ELIGIBLE"
    after = _record(
        value(
            await runtime.bus.query(
                request("project/source/list", "time-basis-after", {"project_id": project_id})
            )
        )
    )
    artifact_after = _record(_list(after["artifacts"])[0])
    assessment_after = _record(_list(after["source_times"])[0])
    bindings_after = [_record(item) for item in _list(after["bindings"])]
    assert artifact_after["cutoff_state"] == assessment_after["cutoff_state"] == "ELIGIBLE"
    assert assessment_after["mode"] == "USER_CONFIRMATION"
    assert assessment_after["reason_code"] == "USER_CONFIRMED_ON_OR_BEFORE"
    assert _integer(assessment_after["revision"]) == _integer(assessment["revision"]) + 1
    for key in ("artifact_id", "source_uri", "media_type", "byte_sha256", "authority"):
        assert artifact_after[key] == artifact[key]
    for key in ("artifact_id", "source_version_id", "byte_sha256"):
        assert assessment_after[key] == assessment[key]
    assert bindings_after == bindings_before
    evidence_after = _record(
        value(
            await runtime.bus.query(
                request("evidence/list", "time-evidence-after", {"project_id": project_id})
            )
        )
    )
    spans_after = [_record(item) for item in _list(evidence_after["spans"])]
    assert len(spans_after) == len(spans_before)
    by_id_before = {_text(item["span_id"]): item for item in spans_before}
    for item in spans_after:
        prior = by_id_before[_text(item["span_id"])]
        assert item["cutoff_state"] == "ELIGIBLE"
        for key in (
            "span_id",
            "artifact_id",
            "source_version_id",
            "locator",
            "text_sha256",
            "exact_text",
            "authority_state",
        ):
            assert item[key] == prior[key]
    source_after = SqliteEvidenceGraphStore(runtime.ledger.engine).read_source_by_artifact(
        _text(artifact["artifact_id"])
    )
    assert source_after is not None
    assert source_after.supersedes_source_id == source_before.source_id
    assert source_after.cutoff_eligibility.value == "ELIGIBLE"
    return source_after.source_id


async def setup(
    workspace: Path, *, confirm_source_time: bool = True
) -> tuple[AppRuntime, dict[str, Any]]:
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text(
        "# Result\n\nThe interface clock drifted.\n\nA second independent clock stayed stable.\n",
        encoding="utf-8",
    )
    runtime, project = await prepare_project(workspace)
    connected = value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "source",
                {
                    "project_id": project,
                    "relative_path": "evidence.md",
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    source_id = (
        await _confirm_setup_source_time(runtime, project)
        if confirm_source_time
        else connected["source"]["source_id"]
    )
    spans = value(
        await runtime.bus.dispatch(
            request(
                "evidence/list",
                "spans",
                {"project_id": project},
            )
        )
    )["spans"]
    started = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "thread",
                {
                    "project_id": project,
                    "thread_id": "thread:a03-storage",
                    "problem": "Why did the interface clock drift?",
                    "scope": {"workstream": "integration"},
                },
            )
        )
    )
    return runtime, {
        "project": project,
        "object": started["current_object_ids"][0],
        "source": source_id,
        "spans": [item["span_id"] for item in spans],
    }


def creation(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": ctx["project"],
        "object_id": ctx["object"],
        "portfolio_id": "portfolio:a03-storage",
        "statement": "Clock configuration may explain the drift",
        "primary_intent": "DIAGNOSTIC_CAUSAL",
        "evidence_basis": "SOURCE",
        "scope": {"workstream": "integration"},
        "evidence_refs": ctx["spans"],
    }


@pytest.mark.asyncio
async def test_undated_source_is_not_eligible_without_explicit_confirmation(
    tmp_path: Path,
) -> None:
    runtime, ctx = await setup(tmp_path, confirm_source_time=False)
    try:
        project_id = _text(ctx["project"])
        listed = _record(
            value(
                await runtime.bus.query(
                    request(
                        "project/source/list", "unknown-time-source", {"project_id": project_id}
                    )
                )
            )
        )
        artifact = _record(_list(listed["artifacts"])[0])
        assessment = _record(_list(listed["source_times"])[0])
        assert artifact["cutoff_state"] == assessment["cutoff_state"] == "UNKNOWN_TIME"
        assert assessment["reason_code"] == "NO_DOCUMENT_DATE"
        evidence = _record(
            value(
                await runtime.bus.query(
                    request("evidence/list", "unknown-time-evidence", {"project_id": project_id})
                )
            )
        )
        spans = [_record(item) for item in _list(evidence["spans"])]
        assert spans and all(item["cutoff_state"] == "UNKNOWN_TIME" for item in spans)
        before = domain_snapshot(runtime.ledger.engine)
        heads_before = runtime.ledger.read_heads(project_id)
        rejected = await runtime.bus.dispatch(
            request("hypothesis/create", "unknown-time-hypothesis", creation(ctx))
        )
        assert rejected.error is not None
        assert rejected.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert "evidence cannot enter hypothesis context" in rejected.error.message
        assert runtime.ledger.read_heads(project_id) == heads_before
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def create_hypothesis(runtime: AppRuntime, ctx: dict[str, Any]) -> dict[str, Any]:
    return value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/create",
                "hypothesis-create",
                creation(ctx),
            )
        )
    )["hypothesis"]


def link_input(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": ctx["project"],
        "target_type": "DECISION_OBJECT",
        "target_id": ctx["object"],
        "relation": "SUPPORTS",
        "span_ids": ctx["spans"],
        "observed_statement": "The interface clock drifted",
        "independence_group": "source:a03",
    }


async def create_link(runtime: AppRuntime, ctx: dict[str, Any]) -> dict[str, Any]:
    return value(
        await runtime.bus.dispatch(
            request(
                "evidence/link/propose",
                "link",
                link_input(ctx),
            )
        )
    )["evidence"]


def assert_reopened(workspace: Path, baseline: dict[str, tuple[str, ...]]) -> None:
    reopened = create_runtime(workspace)
    try:
        assert domain_snapshot(reopened.ledger.engine) == baseline
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["add_hypothesis", "append_audit"])
async def test_public_hypothesis_create_rolls_back_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    baseline = domain_snapshot(runtime.ledger.engine)
    try:
        fail_after(monkeypatch, SqliteHypothesisStore, point)
        response = await runtime.bus.dispatch(request("hypothesis/create", "fault", creation(ctx)))
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["add_assumption", "add_prediction", "add_appraisal"])
async def test_public_auxiliary_write_rolls_back_with_hypothesis_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    current = await create_hypothesis(runtime, ctx)
    measurement = (
        await connect_local_measurement_contract(runtime, ctx["project"], tmp_path)
        if point == "add_prediction"
        else ""
    )
    common = {"project_id": ctx["project"], "hypothesis_id": current["hypothesis_id"]}
    routes: dict[str, tuple[str, dict[str, Any]]] = {
        "add_assumption": (
            "hypothesis/assumption/add",
            {
                **common,
                "expected_revision_digest": current["revision_digest"],
                "statement": "Measurement clock is calibrated",
                "role": "MEASUREMENT",
                "evidence_refs": ctx["spans"],
                "validation_route": "calibration record",
            },
        ),
        "add_prediction": (
            "hypothesis/prediction/bind",
            {
                **common,
                "hypothesis_revision_digest": current["revision_digest"],
                "knowledge_cutoff": "2026-09-05T00:00:00Z",
                "prespecification_state": "A_PRIORI",
                "conditions": {"dataset_version": "trial-v1"},
                "measurement_contract_ref": measurement,
                "assumption_refs": [],
                "expected_outcome": {
                    "type": "RANGE",
                    "measure": "latency",
                    "unit": "ms",
                    "lower": 9,
                    "upper": 13,
                },
                "discrimination_map": {},
            },
        ),
        "add_appraisal": (
            "hypothesis/appraise",
            {
                **common,
                "evidence_refs": ctx["spans"],
                "test_assessment_refs": [],
                "appraisal_scope": {"workstream": "integration"},
            },
        ),
    }
    baseline = domain_snapshot(runtime.ledger.engine)
    try:
        original = getattr(SqliteHypothesisStore, point)
        hits: list[str] = []

        def injected(store: SqliteHypothesisStore, *args: Any, **kwargs: Any) -> None:
            original(store, *args, **kwargs)
            hits.append(point)
            raise RuntimeError("injected auxiliary post-write failure")

        monkeypatch.setattr(SqliteHypothesisStore, point, injected)
        method, payload = routes[point]
        response = await runtime.bus.dispatch(request(method, "auxiliary-fault", payload))
        assert response.error is not None
        assert hits == [point]
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_undated_measurement_contract_rejects_prediction_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        current = await create_hypothesis(runtime, ctx)
        measurement = await connect_local_measurement_contract(
            runtime, ctx["project"], tmp_path, confirm_source_time=False
        )
        listed = value(
            await runtime.bus.query(
                request(
                    "project/source/list", "undated-measurement", {"project_id": ctx["project"]}
                )
            )
        )
        assessment = next(
            item for item in listed["source_times"] if item["artifact_id"] == measurement
        )
        assert assessment["cutoff_state"] == "UNKNOWN_TIME"
        assert assessment["reason_code"] == "NO_DOCUMENT_DATE"
        baseline = domain_snapshot(runtime.ledger.engine)
        heads_before = runtime.ledger.read_heads(ctx["project"])
        original = SqliteHypothesisStore.add_prediction
        hits: list[str] = []

        def observed(store: SqliteHypothesisStore, *args: Any, **kwargs: Any) -> None:
            hits.append("add_prediction")
            original(store, *args, **kwargs)

        monkeypatch.setattr(SqliteHypothesisStore, "add_prediction", observed)
        rejected = await runtime.bus.dispatch(
            request(
                "hypothesis/prediction/bind",
                "undated-measurement-prediction",
                {
                    "project_id": ctx["project"],
                    "hypothesis_id": current["hypothesis_id"],
                    "hypothesis_revision_digest": current["revision_digest"],
                    "knowledge_cutoff": "2026-09-05T00:00:00Z",
                    "prespecification_state": "A_PRIORI",
                    "conditions": {"dataset_version": "trial-v1"},
                    "measurement_contract_ref": measurement,
                    "assumption_refs": [],
                    "expected_outcome": {
                        "type": "RANGE",
                        "measure": "latency",
                        "unit": "ms",
                        "lower": 9,
                        "upper": 13,
                    },
                    "discrimination_map": {},
                },
            )
        )
        assert rejected.error is not None
        assert rejected.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert "MEASUREMENT_CONTRACT_SOURCE_UNAVAILABLE" in rejected.error.message
        assert hits == []
        assert domain_snapshot(runtime.ledger.engine) == baseline
        assert runtime.ledger.read_heads(ctx["project"]) == heads_before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_public_generation_rolls_back_every_item_when_second_item_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    baseline = domain_snapshot(runtime.ledger.engine)
    original = SqliteHypothesisStore.add_hypothesis
    calls = 0

    def fault(store: SqliteHypothesisStore, record: HypothesisRecord) -> None:
        nonlocal calls
        original(store, record)
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second candidate post-write failure")

    try:
        monkeypatch.setattr(SqliteHypothesisStore, "add_hypothesis", fault)
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/generate",
                "generate-fault",
                {
                    "project_id": ctx["project"],
                    "object_id": ctx["object"],
                    "question": "Why did the clock drift?",
                    "evidence_scope": ctx["spans"],
                    "intent_hints": ["DIAGNOSTIC_CAUSAL"],
                    "generation_policy_ref": "generation:bounded",
                },
            )
        )
        assert response.error is not None
        assert calls == 2
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,point",
    [
        ("evidence/source/metadata/correct", "add_source"),
        ("evidence/source/metadata/correct", "append_audit"),
        ("evidence/challenge", "add_conflict"),
        ("evidence/revalidate", "add_link"),
        ("evidence/span/correct", "add_span_correction"),
    ],
)
async def test_public_evidence_record_and_audit_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    point: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    link = await create_link(runtime, ctx)
    payloads: dict[str, dict[str, Any]] = {
        "evidence/source/metadata/correct": {"source_id": ctx["source"], "rights": "INTERNAL"},
        "evidence/challenge": {
            "evidence_ids": [link["evidence_id"]],
            "field": "clock",
            "reason": "drift",
        },
        "evidence/revalidate": {"evidence_id": link["evidence_id"]},
        "evidence/span/correct": {
            "span_id": ctx["spans"][0],
            "corrected_text": "Clock drift observed",
            "reason": "spelling correction",
        },
    }
    baseline = domain_snapshot(runtime.ledger.engine)
    try:
        fail_after(monkeypatch, SqliteEvidenceGraphStore, point)
        response = await runtime.bus.dispatch(
            request(
                method,
                "evidence-fault",
                {
                    "project_id": ctx["project"],
                    **payloads[method],
                },
            )
        )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_hypothesis_duplicate_key_and_stale_revision_preserve_snapshot(
    tmp_path: Path,
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        first = await create_hypothesis(runtime, ctx)
        baseline = domain_snapshot(runtime.ledger.engine)
        replay = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/create",
                    "hypothesis-create",
                    creation(ctx),
                )
            )
        )
        assert replay["hypothesis"]["hypothesis_id"] == first["hypothesis_id"]
        assert domain_snapshot(runtime.ledger.engine) == baseline
        payload: dict[str, object] = {
            "project_id": ctx["project"],
            "hypothesis_id": first["hypothesis_id"],
            "expected_revision_digest": first["revision_digest"],
            "patch": {"statement": "A revised clock explanation"},
            "evidence_refs": ctx["spans"],
            "reason": "new evidence",
        }
        value(await runtime.bus.dispatch(request("hypothesis/revise", "revise", payload)))
        baseline = domain_snapshot(runtime.ledger.engine)
        rejected = await runtime.bus.dispatch(request("hypothesis/revise", "stale", payload))
        assert rejected.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_public_create_rejects_reused_entity_id_without_overwriting_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    current = await create_hypothesis(runtime, ctx)
    baseline = domain_snapshot(runtime.ledger.engine)
    original = UuidIdGenerator.new

    def collide(ids: UuidIdGenerator, prefix: str) -> str:
        return str(current["hypothesis_id"]) if prefix == "hypothesis" else original(ids, prefix)

    try:
        monkeypatch.setattr(UuidIdGenerator, "new", collide)
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/create",
                "collision",
                {
                    **creation(ctx),
                    "statement": "A different candidate",
                },
            )
        )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_revision_race_after_public_read_cannot_leave_branch_or_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    current = await create_hypothesis(runtime, ctx)
    original = HypothesisService.revise
    accepted: list[dict[str, tuple[str, ...]]] = []

    def race(service: HypothesisService, record: HypothesisRecord, **kwargs: Any) -> Any:
        original(
            service,
            record,
            updates={"statement": "Concurrent accepted revision"},
            event_type="hypothesis/updated",
        )
        accepted.append(domain_snapshot(runtime.ledger.engine))
        return original(service, record, **kwargs)

    try:
        monkeypatch.setattr(HypothesisService, "revise", race)
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/revise",
                "racing-revision",
                {
                    "project_id": ctx["project"],
                    "hypothesis_id": current["hypothesis_id"],
                    "expected_revision_digest": current["revision_digest"],
                    "patch": {"statement": "Stale competing revision"},
                    "evidence_refs": ctx["spans"],
                    "reason": "race",
                },
            )
        )
        assert response.error is not None
        assert len(accepted) == 1
        assert domain_snapshot(runtime.ledger.engine) == accepted[0]
    finally:
        runtime.close()
    assert_reopened(tmp_path, accepted[0])


@pytest.mark.asyncio
async def test_tied_hypothesis_timestamp_selects_latest_committed_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:

        def fixed_clock(clock: SystemClock) -> datetime:
            return datetime(2026, 9, 1, tzinfo=UTC)

        monkeypatch.setattr(SystemClock, "now", fixed_clock)
        current = await create_hypothesis(runtime, ctx)
        revised = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/revise",
                    "same-time",
                    {
                        "project_id": ctx["project"],
                        "hypothesis_id": current["hypothesis_id"],
                        "expected_revision_digest": current["revision_digest"],
                        "patch": {"statement": "Latest clock explanation"},
                        "evidence_refs": ctx["spans"],
                        "reason": "same-clock-tick",
                    },
                )
            )
        )["hypothesis"]
        read = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "latest",
                    {
                        "project_id": ctx["project"],
                        "hypothesis_id": current["hypothesis_id"],
                    },
                )
            )
        )
        assert read["hypothesis"]["revision_digest"] == revised["revision_digest"]
        baseline = domain_snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["source", "link", "link_correct"])
async def test_evidence_duplicate_key_and_stale_predecessor_preserve_snapshot(
    tmp_path: Path,
    kind: str,
) -> None:
    runtime, ctx = await setup(tmp_path)
    link = await create_link(runtime, ctx)
    method = {
        "source": "evidence/source/metadata/correct",
        "link": "evidence/revalidate",
        "link_correct": "evidence/link/correct",
    }[kind]
    payload: dict[str, object] = {
        "project_id": ctx["project"],
        **(
            {"source_id": ctx["source"], "rights": "INTERNAL"}
            if kind == "source"
            else {"evidence_id": link["evidence_id"]}
        ),
    }
    if kind == "link_correct":
        payload = {**link_input(ctx), "evidence_id": link["evidence_id"]}
    try:
        first = value(await runtime.bus.dispatch(request(method, "advance", payload)))
        baseline = domain_snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(request(method, "advance", payload))) == first
        assert domain_snapshot(runtime.ledger.engine) == baseline
        response = await runtime.bus.dispatch(request(method, "stale", payload))
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == baseline
    finally:
        runtime.close()
    assert_reopened(tmp_path, baseline)


@pytest.mark.asyncio
async def test_public_hypothesis_audit_digest_binds_stored_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, ctx = await setup(tmp_path)
    ticks = count()
    started_at = datetime(2026, 9, 1, tzinfo=UTC)

    def advancing_clock(clock: SystemClock) -> datetime:
        return started_at + timedelta(seconds=next(ticks))

    try:
        monkeypatch.setattr(SystemClock, "now", advancing_clock)
        hypothesis = await create_hypothesis(runtime, ctx)
        query = {
            "project_id": ctx["project"],
            "hypothesis_id": hypothesis["hypothesis_id"],
        }
        records = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/audit/read",
                    "audit-digest",
                    query,
                )
            )
        )["records"]
        assert len(records) == 1
        stored = HypothesisAuditRecord.model_validate(records[0])
        recomputed = domain_digest(
            "HYPOTHESIS_AUDIT",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": stored.project_id,
                    "hypothesis_id": stored.hypothesis_id,
                    "event_type": stored.event_type,
                    "payload": stored.payload,
                    "created_at": stored.created_at,
                }
            ),
        )
        assert stored.event_digest == recomputed
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        reread = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/audit/read",
                    "audit-digest-reopen",
                    query,
                )
            )
        )["records"]
        assert reread == records
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_public_evidence_audit_digest_binds_stored_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ctx = await setup(tmp_path)
    ticks = count()

    def advancing_clock(clock: SystemClock) -> datetime:
        return datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))

    try:
        monkeypatch.setattr(SystemClock, "now", advancing_clock)
        corrected = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/metadata/correct",
                    "audit-source-correct",
                    {
                        "project_id": ctx["project"],
                        "source_id": ctx["source"],
                        "rights": "INTERNAL",
                    },
                )
            )
        )["source"]
        query = {"project_id": ctx["project"], "evidence_ref": corrected["source_id"]}
        records = value(
            await runtime.bus.dispatch(request("evidence/audit/read", "audit-check", query))
        )["records"]
        assert len(records) == 1
        stored = EvidenceAuditRecord.model_validate(records[0])
        assert stored.event_digest == domain_digest(
            "EVIDENCE_AUDIT",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": stored.project_id,
                    "evidence_ref": stored.evidence_ref,
                    "event_type": stored.event_type,
                    "payload": stored.payload,
                    "created_at": stored.created_at,
                }
            ),
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        reread = value(
            await reopened.bus.dispatch(request("evidence/audit/read", "audit-reopen", query))
        )["records"]
        assert reread == records
    finally:
        reopened.close()
