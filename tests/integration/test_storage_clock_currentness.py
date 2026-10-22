"""F-03b: real store appends select B after A despite a backwards or tied wall clock.

No application runtime, server or model is constructed. Optional canonical-ledger stores are
exercised in their direct projection mode; this is not a claim that canonical head reads are broken.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, literal_column, select
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.thread_runtime import SqliteThreadRuntimeStore
from thoth.domain.action_full import (
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.base import DomainModel
from thoth.domain.control_record import ControlRecord
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.decision_object_full import (
    DecisionObjectRecord,
    ObjectAttentionRecord,
    ObjectRelationRecord,
)
from thoth.domain.event import Checkpoint
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.hypothesis_full import (
    HypothesisPortfolioRecord,
    HypothesisRecord,
    HypothesisRelationRecord,
)
from thoth.domain.outcome_full import OutcomeAssessmentRecord, OutcomeSeriesRecord
from thoth.domain.thread_runtime import ThreadCheckpoint
from thoth.ports.model import ModelPort

STAMP = datetime(2026, 10, 23, 12, tzinfo=UTC)
COMMON: dict[str, object] = {
    "project_id": "project:f03b",
    "object_id": "object:f03b",
    "source_object_id": "object:f03b",
    "thread_id": "thread:f03b",
    "operation_id": "operation:f03b",
    "cycle_id": "cycle:f03b",
    "portfolio_id": "portfolio:f03b",
    "plan_id": "plan:f03b",
    "action_id": "action:f03b",
    "authorization_id": "authorization:f03b",
    "plan_execution_id": "execution:f03b",
    "attempt_id": "attempt:f03b",
    "step_id": "step:f03b",
    "outcome_series_id": "series:f03b",
    "outcome_assessment_id": "assessment:f03b",
    "criterion_id": "criterion:f03b",
    "hypothesis_id": "hypothesis:f03b",
    "source_hypothesis_id": "hypothesis:f03b",
    "target_hypothesis_id": "hypothesis:other",
    "relation_id": "relation:f03b",
    "attention_id": "attention:f03b",
    "artifact_id": "artifact:f03b",
    "record_id": "record:f03b",
    "actor_or_agent_ref": "actor:synthetic",
    "created_at": STAMP,
    "updated_at": STAMP,
    "valid_from": STAMP,
    "expires_at": STAMP + timedelta(days=1),
    "revision_digest": "1" * 64,
    "checkpoint_digest": "1" * 64,
    "head_set_digest": "1" * 64,
    "resume_token": "1" * 64,
    "plan_revision_digest": "1" * 64,
    "action_plan_revision_digest": "1" * 64,
    "expected_working_head_digest": "1" * 64,
    "exact_scope_digest": "1" * 64,
    "exact_invocation_digest": "1" * 64,
    "environment_digest": "1" * 64,
    "comparison_baseline_set_digest": "1" * 64,
    "baseline_set_digest": "1" * 64,
    "record_digest": "1" * 64,
    "source_digest": "1" * 64,
    "sha256": "1" * 64,
    "revision": 1,
    "profile_ref": "profile:synthetic",
    "evidence_refs": (),
    "required_roles": (),
    "payload": {"revision": "A"},
}


@dataclass(frozen=True)
class StoreCase:
    table: str
    model: type[DomainModel]
    store: Callable[[Engine], Any]
    append: str
    revision_field: str
    read: str
    read_fields: tuple[str | None, ...]
    extra: dict[str, object] = field(default_factory=dict)
    latest_list: str | None = None
    list_fields: tuple[str, ...] = ("project_id",)
    digest_field: str = "revision_digest"
    read_last: bool = False
    time_field: str = "created_at"


CASES = (
    StoreCase(
        "action_records",
        ActionRecord,
        SqliteActionStore,
        "add_action",
        "action_revision_id",
        "read_action",
        ("project_id", "action_id", None),
        {
            "primary_purpose": None,
            "proposal_state": "DRAFT",
            "specification": {},
            "expected_observation_or_change": {},
            "effect_vector": {},
            "impact_set": {},
            "risk_tier": "R0",
            "required_processes": (),
        },
        "list_actions",
    ),
    StoreCase(
        "action_portfolios",
        ActionPortfolioRecord,
        SqliteActionStore,
        "add_portfolio",
        "portfolio_revision_id",
        "read_portfolio",
        ("project_id", "portfolio_id", None),
        {
            "action_refs": (),
            "decision_need": "synthetic",
            "mandatory_criteria": (),
            "enhancing_criteria": (),
        },
        "list_portfolios",
    ),
    StoreCase(
        "action_plans",
        ActionPlanRecord,
        SqliteActionStore,
        "add_plan",
        "plan_revision_id",
        "read_plan",
        ("project_id", "plan_id", None),
        {
            "selected_action_refs": (),
            "steps": (),
            "dependency_edges": (),
            "cumulative_impact": {},
            "required_process_union": (),
            "required_role_union": (),
            "auto_executable_frontier": (),
            "point_of_no_return_steps": (),
        },
        "list_plans",
    ),
    StoreCase(
        "action_authorizations",
        AuthorizationEnvelopeRecord,
        SqliteActionStore,
        "add_authorization",
        "authorization_revision_id",
        "read_authorization",
        ("project_id", "authorization_id"),
        {"predecessor_output_digests": (), "target_baseline_digests": (), "policy_version": "1"},
        "list_authorizations",
        ("project_id", "plan_id"),
    ),
    StoreCase(
        "plan_executions",
        PlanExecutionRecord,
        SqliteExecutionStore,
        "add_execution",
        "execution_revision_id",
        "read_execution",
        ("project_id", "plan_execution_id"),
        {"execution_profile_ref": "synthetic"},
        "list_executions",
        time_field="updated_at",
    ),
    StoreCase(
        "step_execution_attempts",
        StepExecutionAttemptRecord,
        SqliteExecutionStore,
        "add_attempt",
        "attempt_revision_id",
        "read_attempt",
        ("project_id", "attempt_id"),
        {
            "attempt_number": 1,
            "state": "DISPATCHED",
            "input_digests": (),
            "target_digests": (),
            "idempotency_key": "synthetic",
            "delivery_guarantee": "AT_MOST_ONCE",
        },
        "list_attempts",
        ("project_id", "plan_execution_id"),
    ),
    StoreCase(
        "checkpoints",
        Checkpoint,
        SqliteEventStore,
        "insert_checkpoint",
        "checkpoint_id",
        "latest_checkpoint",
        ("operation_id",),
        digest_field="checkpoint_digest",
    ),
    StoreCase(
        "thread_checkpoints",
        ThreadCheckpoint,
        SqliteThreadRuntimeStore,
        "put_checkpoint",
        "checkpoint_id",
        "list_checkpoints",
        ("project_id", "thread_id"),
        digest_field="checkpoint_digest",
        read_last=True,
    ),
    StoreCase(
        "outcome_series",
        OutcomeSeriesRecord,
        SqliteOutcomeStore,
        "add_series",
        "series_revision_id",
        "read_series",
        ("project_id", "outcome_series_id", None),
        {"assessment_windows": (), "phase_states": {}},
        "list_series",
    ),
    StoreCase(
        "outcome_assessments",
        OutcomeAssessmentRecord,
        SqliteOutcomeStore,
        "add_assessment",
        "assessment_revision_id",
        "read_assessment",
        ("project_id", "outcome_assessment_id", None),
        {
            "assessment_phase": "POST",
            "profile_version": 1,
            "actual_observation_refs": (),
            "comparator_refs": (),
            "assumptions": (),
        },
        "list_assessments",
    ),
    StoreCase(
        "decision_objects",
        DecisionObjectRecord,
        SqliteDecisionObjectStore,
        "add_object",
        "object_revision_id",
        "read_object",
        ("project_id", "object_id", None),
        {
            "purpose_statement": "synthetic",
            "problem_frame": "synthetic",
            "focus_refs": (),
            "materialization_trigger": "SYNTHETIC",
            "trigger_evidence_refs": (),
            "entry_criteria": {},
            "exit_criteria": {},
        },
        "list_objects",
    ),
    StoreCase(
        "object_relations",
        ObjectRelationRecord,
        SqliteDecisionObjectStore,
        "add_relation",
        "relation_revision_id",
        "read_relation",
        ("relation_id",),
        {
            "relation_type": "RELATED",
            "target_ref": "object:other",
            "semantic_role": "synthetic",
            "authority_state": "INFORMAL",
        },
        "list_relations",
        ("project_id", "source_object_id"),
    ),
    StoreCase(
        "object_attention",
        ObjectAttentionRecord,
        SqliteDecisionObjectStore,
        "add_attention",
        "attention_revision_id",
        "read_attention",
        ("attention_id",),
        {"attention_type": "REVIEW", "risk": "LOW", "reason": "synthetic", "condition_ref": "c"},
        "list_attention",
        ("project_id", "object_id"),
    ),
    StoreCase(
        "criterion_contracts",
        CriterionContractRecord,
        SqliteCriterionContractStore,
        "add_contract",
        "criterion_revision_id",
        "read_contract",
        ("project_id", "criterion_id", None),
        {
            "identity": {"name": "synthetic"},
            "profile_refs": (),
            "construct_outcome_definition": "x",
            "verification_spec": {},
            "required_evidence": (),
            "field_evidence_map": {},
            "field_authority_and_version": {},
            "governance": {},
        },
        "list_contracts",
    ),
    StoreCase(
        "hypothesis_records",
        HypothesisRecord,
        SqliteHypothesisStore,
        "add_hypothesis",
        "hypothesis_revision_id",
        "read_hypothesis",
        ("project_id", "hypothesis_id", None),
        {
            "statement": "synthetic",
            "observed_problem": "synthetic",
            "primary_intent": None,
            "evidence_basis": "UNKNOWN",
            "scope": {},
        },
        "list_hypotheses",
    ),
    StoreCase(
        "hypothesis_portfolios",
        HypothesisPortfolioRecord,
        SqliteHypothesisStore,
        "add_portfolio",
        "portfolio_revision_id",
        "read_portfolio",
        ("project_id", "portfolio_id", None),
        {"hypothesis_refs": (), "unknown_reserve": {}},
        "list_portfolios",
    ),
    StoreCase(
        "hypothesis_relations",
        HypothesisRelationRecord,
        SqliteHypothesisStore,
        "add_relation",
        "relation_revision_id",
        "read_relation",
        ("relation_id",),
        {"relation_type": "RELATED"},
        "list_relations",
        ("project_id", "portfolio_id"),
    ),
    StoreCase(
        "evidence_sources",
        EvidenceSourceRecord,
        SqliteEvidenceGraphStore,
        "add_source",
        "source_id",
        "read_source_by_artifact",
        ("artifact_id",),
        {
            "connector_ref": "fixture:direct-store",
            "uri": "fixture://f03b",
            "artifact_type": "TEXT",
            "authority_status": "INFORMAL",
            "security_class": "INTERNAL",
            "cutoff_eligibility": "ELIGIBLE",
            "lineage_root_id": "source:root",
        },
        digest_field="source_digest",
    ),
    # The plan classifies this table as safe because it has a monotonic version. Check both APIs.
    StoreCase(
        "control_records",
        ControlRecord,
        SqliteControlRecordStore,
        "append",
        "control_revision_id",
        "read",
        ("project_id", "namespace", "record_id"),
        {"namespace": "MEMORY", "record_type": "CANDIDATE", "version": 1, "state": "CANDIDATE"},
        "list",
        ("project_id", "namespace"),
        digest_field="record_digest",
    ),
)


def revisions(case: StoreCase, offset: int) -> tuple[DomainModel, DomainModel]:
    values = {key: value for key, value in COMMON.items() if key in case.model.model_fields}
    first = case.model.model_validate({**values, **case.extra, case.revision_field: "z:revision-A"})
    second = first.model_dump(mode="python")
    second.update(
        {
            case.revision_field: "a:revision-B",
            case.digest_field: "2" * 64,
            case.time_field: STAMP + timedelta(seconds=offset),
        }
    )
    for name in ("revision", "version"):
        if isinstance(second.get(name), int):
            second[name] = int(str(second[name])) + 1
    if "supersedes_revision_digest" in second:
        second["supersedes_revision_digest"] = getattr(first, case.digest_field)
    if "payload" in second:
        second["payload"] = {"revision": "B"}
    return first, case.model.model_validate(second)


def observe(case: StoreCase, store: Any, first: DomainModel) -> dict[str, list[str]]:
    args = [None if name is None else getattr(first, name) for name in case.read_fields]
    current = getattr(store, case.read)(*args)
    if case.read_last:
        current = current[-1] if current else None
    found = {case.read: [] if current is None else [str(getattr(current, case.revision_field))]}
    if case.latest_list:
        values = getattr(store, case.latest_list)(
            *[getattr(first, key) for key in case.list_fields]
        )
        found[case.latest_list] = [str(getattr(value, case.revision_field)) for value in values]
    if case.read_fields[-1] is None:
        historic = getattr(store, case.read)(*args[:-1], getattr(first, case.digest_field))
        assert historic is not None and historic.model_dump() == first.model_dump()
    return found


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.table)
@pytest.mark.parametrize("offset", [-60, 0], ids=["rollback", "tie"])
def test_latest_is_last_appended_revision(tmp_path: Path, case: StoreCase, offset: int) -> None:
    path = tmp_path / "store.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    table = metadata.tables[case.table]
    table.create(engine)
    try:
        first, second = revisions(case, offset)
        store = case.store(engine)
        getattr(store, case.append)(first)
        getattr(store, case.append)(second)
        with engine.connect() as connection:
            persisted = [
                dict(row)
                for row in connection.execute(
                    select(literal_column("rowid"), table).order_by(literal_column("rowid"))
                ).mappings()
            ]
        assert len(persisted) == 2
        assert [datetime.fromisoformat(str(row["created_at"])) for row in persisted] == [
            STAMP,
            STAMP + timedelta(seconds=offset),
        ]
        observed = observe(case, store, first)
    finally:
        engine.dispose()
    reopened = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        after_reopen = observe(case, case.store(reopened), first)
        assert after_reopen == observed
    finally:
        reopened.dispose()
    expected = {name: [str(getattr(second, case.revision_field))] for name in observed}
    folder = (
        Path(__file__).resolve().parents[2]
        / "outputs/f03b-20261023"
        / os.environ.get("THOTH_F03B_PHASE", "direct")
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{case.table}-{'rollback' if offset else 'tie'}.json").write_text(
        json.dumps(
            {
                "table": case.table,
                "offset_seconds": offset,
                "persisted": persisted,
                "observed": observed,
                "expected": expected,
                "reopen_equal": True,
                "paths": {
                    name: "PASS" if observed[name] == expected[name] else "FAIL"
                    for name in observed
                },
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    assert observed == expected


@pytest.mark.parametrize("table_name", ["plan_executions", "outcome_series"])
@pytest.mark.parametrize(
    "mode", ["higher_revision", "counter_tie_rollback", "counter_tie_same_time"]
)
def test_counter_order_precedes_time_then_uses_append_order(
    tmp_path: Path, table_name: str, mode: str
) -> None:
    case = next(case for case in CASES if case.table == table_name)
    offset = 60 if mode == "higher_revision" else -60 if mode == "counter_tie_rollback" else 0
    first, second = revisions(case, offset)
    first = case.model.model_validate({**first.model_dump(), "revision": 2})
    second = case.model.model_validate(
        {**second.model_dump(), "revision": 1 if mode == "higher_revision" else 2}
    )
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'counter.sqlite3').as_posix()}")
    metadata.tables[case.table].create(engine)
    try:
        store = case.store(engine)
        getattr(store, case.append)(first)
        getattr(store, case.append)(second)
        selected = first if mode == "higher_revision" else second
        expected = str(getattr(selected, case.revision_field))
        assert all(found == [expected] for found in observe(case, store, first).values())
    finally:
        engine.dispose()


class NoModels:
    calls = 0

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        self.calls += 1
        raise AssertionError("F03b metadata flow must not resolve a model")


async def test_pause_uses_latest_append_while_checkpoint_history_keeps_display_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": STAMP}

    def tick(self: SystemClock) -> datetime:
        now = clock["now"]
        clock["now"] += timedelta(microseconds=1)
        return now

    monkeypatch.setattr(SystemClock, "now", tick)
    models = NoModels()
    runtime = create_runtime(tmp_path / "workspace", model_resolver=models)
    project, thread = "project:checkpoint-clock", "thread:checkpoint-clock"
    body: dict[str, object] = {"project_id": project, "thread_id": thread}

    async def call(method: str, key: str, payload: dict[str, object]) -> dict[str, Any]:
        return value(await runtime.bus.dispatch(request(method, key, payload)))

    try:
        await call(
            "project/create",
            "project",
            {"project_id": project, "name": "Clock ordering", "cutoff_at": "2026-10-01T00:00:00Z"},
        )
        await call(
            "thread/start", "thread", {**body, "problem": "Check clock ordering", "scope": {}}
        )
        first = (await call("thread/pause", "pause-A", body))["checkpoint"]
        clock["now"] = STAMP - timedelta(minutes=1)
        await call("thread/resume", "resume-A", body)
        second = (await call("thread/pause", "pause-B", body))["checkpoint"]
        assert first["created_at"] > second["created_at"]
        history = (await call("thread/checkpoint/list", "history", body))["checkpoints"]
        records = SqliteThreadRuntimeStore(runtime.ledger.engine).list_checkpoints(project, thread)
        expected = [
            record.model_dump(mode="json")
            for record in sorted(
                records, key=lambda record: (record.created_at.isoformat(), record.checkpoint_id)
            )
        ]
        assert history == expected
        assert (await call("thread/pause", "already-paused", body))["checkpoint"] == second
        resumed = await call(
            "thread/resume",
            "resume-B",
            {**body, "expected_checkpoint_digest": second["checkpoint_digest"]},
        )
        assert resumed["execution_state"] == "IDLE"
    finally:
        runtime.close()
    assert models.calls == 0


@pytest.mark.parametrize("mode", ["higher_version", "tie_rollback", "tie_same_time", "tie_forward"])
def test_control_current_version_ties_and_display_history(tmp_path: Path, mode: str) -> None:
    case = next(case for case in CASES if case.table == "control_records")
    offset = -60 if mode == "tie_rollback" else 0 if mode == "tie_same_time" else 60
    first, second = revisions(case, offset)
    first = ControlRecord.model_validate(
        {**first.model_dump(), "version": 3 if mode == "higher_version" else 2}
    )
    second = ControlRecord.model_validate({**second.model_dump(), "version": 2})
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'control.sqlite3').as_posix()}")
    metadata.tables[case.table].create(engine)
    try:
        store = SqliteControlRecordStore(engine)
        store.append(first)
        store.append(second)
        current = store.read(first.project_id, first.namespace, first.record_id)
        latest = store.list(first.project_id, first.namespace)
        history = store.list(first.project_id, first.namespace, latest_only=False)
        expected = first if mode == "higher_version" else second
        expected_history = tuple(
            sorted(
                (first, second), key=lambda record: (record.created_at.isoformat(), record.version)
            )
        )
        folder = (
            Path(__file__).resolve().parents[2]
            / "outputs/f03b-20261023"
            / os.environ.get("THOTH_F03B_PHASE", "direct")
        )
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"control-{mode}.json").write_text(
            json.dumps(
                {
                    "mode": mode,
                    "current": None if current is None else current.control_revision_id,
                    "latest": [record.control_revision_id for record in latest],
                    "history": [record.control_revision_id for record in history],
                    "expected_current": expected.control_revision_id,
                    "expected_history": [record.control_revision_id for record in expected_history],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        assert history == expected_history
        assert current == expected
        assert latest == (expected,)
    finally:
        engine.dispose()
