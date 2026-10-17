"""A real second counter publication rolls back without removing the first wave."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver, request, value
from tests.integration.test_a03_critical_counter_search import A03Connector, CriticalA03Model
from tests.integration.test_a03_multi_wave_counter_search import policy, route

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.acquisition_trace import SqliteEvidenceUnitOfWork
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.schema import (
    evidence_audit,
    evidence_leads,
    evidence_links,
    evidence_observations,
    evidence_sources,
    source_bindings,
)
from thoth.adapters.storage.transaction import read_connection
from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.acquisition import EvidenceAtomicCommit
from thoth.domain.governance import SourceBinding
from thoth.domain.investigation import InvestigationRecord
from thoth.ports.model import ModelPort


@pytest.mark.parametrize(
    "point", ("after_observation", "after_lead", "after_claim_candidate", "after_audit")
)
async def test_second_counter_publication_failure_preserves_first_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    primary = A03Connector(
        "mw-primary",
        {"primary.md": b"# Primary\n\nCurrent evidence supports the dominant explanation.\n"},
    )
    first = A03Connector(
        "mw-one",
        {"first.md": b"# Independent one\n\nfalsifies dominant first independent finding\n"},
    )
    second = A03Connector(
        "mw-two",
        {"second.md": b"# Independent two\n\nfalsifies dominant second independent finding\n"},
    )
    candidates: list[EvidenceAtomicCommit] = []
    rolled_back: list[EvidenceAtomicCommit] = []
    trace: list[str] = []
    fired: list[str] = []
    failed_binding: list[str] = []

    def fault(stage: str) -> None:
        if len(candidates) != 2 or stage != point:
            return
        pending = candidates[-1]
        assert pending.lead is not None
        with read_connection(runtime.ledger.engine) as connection:
            assert (
                connection.execute(
                    select(evidence_sources.c.connector_ref).where(
                        evidence_sources.c.source_id == pending.lead.source_id
                    )
                ).scalar_one()
                == "mw-two"
            )
            assert (
                connection.execute(
                    select(evidence_observations.c.observation_id).where(
                        evidence_observations.c.observation_id == pending.observation.observation_id
                    )
                ).scalar_one()
                == pending.observation.observation_id
            )
            if stage != "after_observation":
                assert (
                    connection.execute(
                        select(evidence_leads.c.lead_id).where(
                            evidence_leads.c.lead_id == pending.lead.lead_id
                        )
                    ).scalar_one()
                    == pending.lead.lead_id
                )
            if stage in {"after_claim_candidate", "after_audit"}:
                assert (
                    connection.execute(
                        select(evidence_links.c.evidence_id).where(
                            evidence_links.c.evidence_id == pending.claim_candidate.evidence_id
                        )
                    ).scalar_one()
                    == pending.claim_candidate.evidence_id
                )
            if stage == "after_audit":
                expected = {record.audit_id for record in pending.audit_records}
                assert (
                    set(
                        connection.execute(
                            select(evidence_audit.c.audit_id).where(
                                evidence_audit.c.audit_id.in_(expected)
                            )
                        ).scalars()
                    )
                    == expected
                )
        fired.append(stage)
        raise RuntimeError("fixture second counter publication fault")

    runtime = create_runtime(
        tmp_path,
        connector_registry=ConnectorRegistry((primary, first, second)),
        model_resolver=StaticModelResolver(cast(ModelPort, CriticalA03Model())),
        evidence_fault_injector=fault,
    )
    project, thread = "project:counter-atomicity", "thread:counter-atomicity"
    commit = SqliteEvidenceUnitOfWork.commit
    detach = SqliteGovernanceStore.set_source_binding_state
    wave = InvestigationService.record_wave

    def observed_commit(unit: SqliteEvidenceUnitOfWork, pending: EvidenceAtomicCommit) -> None:
        if pending.claim_candidate.applicability != "INDEPENDENT_CRITICAL_REVIEW_CANDIDATE":
            return commit(unit, pending)
        candidates.append(pending)
        before = snapshot(runtime.ledger.engine)
        try:
            commit(unit, pending)
        except RuntimeError as error:
            assert len(candidates) == 2 and str(error) == "fixture second counter publication fault"
            assert_phase_delta(before, snapshot(runtime.ledger.engine))
            rolled_back.append(pending)
            trace.append("rollback:2")
            raise
        trace.append("commit:1")

    def detached(
        store: SqliteGovernanceStore,
        project_id: str,
        binding_id: str,
        *,
        state: str,
        updated_at: str,
    ) -> SourceBinding:
        assert len(rolled_back) == 1 and project_id == project and state == "DETACHED"
        pending = rolled_back[0]
        assert pending.lead is not None
        with runtime.ledger.engine.connect() as connection:
            artifact = connection.execute(
                select(evidence_sources.c.artifact_id).where(
                    evidence_sources.c.source_id == pending.lead.source_id
                )
            ).scalar_one()
            assert (
                connection.execute(
                    select(source_bindings.c.binding_id).where(
                        source_bindings.c.artifact_id == artifact
                    )
                ).scalar_one()
                == binding_id
            )
        result = detach(store, project_id, binding_id, state=state, updated_at=updated_at)
        with runtime.ledger.engine.connect() as connection:
            assert (
                connection.execute(
                    select(source_bindings.c.updated_at).where(
                        source_bindings.c.binding_id == binding_id
                    )
                ).scalar_one()
                == updated_at
            )
        failed_binding.append(binding_id)
        trace.append("detach:2")
        return result

    def recorded_wave(
        service: InvestigationService,
        value: InvestigationRecord,
        *,
        observation_count: int,
        lead_count: int,
        claim_candidate_count: int,
        remaining_target_gaps: tuple[str, ...],
    ) -> InvestigationRecord:
        result = wave(
            service,
            value,
            observation_count=observation_count,
            lead_count=lead_count,
            claim_candidate_count=claim_candidate_count,
            remaining_target_gaps=remaining_target_gaps,
        )
        expected = (1, 1, 1) if value.current_wave == 0 else (0, 0, 0)
        assert (observation_count, lead_count, claim_candidate_count) == expected
        trace.append("wave:" + str(result.current_wave))
        return result

    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "Counter atomicity",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        routes = [
            route("first", "mw-one", "first.md", priority=100),
            route("second", "mw-two", "second.md", priority=90),
        ]
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {
                        "project_id": project,
                        "expected_revision": 0,
                        "payload": policy("converged", routes),
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "primary",
                    {
                        "project_id": project,
                        "connector_id": "mw-primary",
                        "selector": {"relative_path": "primary.md"},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "problem": "Run bounded multi-wave independent counter research.",
                        "scope": {"workstream": "multi-wave-critical"},
                    },
                )
            )
        )
        with monkeypatch.context() as patch:
            patch.setattr(SqliteEvidenceUnitOfWork, "commit", observed_commit)
            patch.setattr(SqliteGovernanceStore, "set_source_binding_state", detached)
            patch.setattr(InvestigationService, "record_wave", recorded_wave)
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "counter-fault",
                        {"project_id": project, "thread_id": thread},
                    )
                )
            )
        assert (
            fired == [point]
            and len(candidates) == 2
            and len(rolled_back) == len(failed_binding) == 1
        )
        assert trace == ["commit:1", "wave:1", "rollback:2", "detach:2", "wave:2"]
        projection = cast(dict[str, JsonValue], result["critical_counter_search"])
        assert projection["loop_terminal"] == "SEARCH_SATURATED"
        budget = cast(dict[str, JsonValue], projection["budget"])
        assert (
            budget["used_waves"] == budget["used_documents"] == 2 and budget["used_tool_calls"] == 4
        )
        assert first.fetch_count == second.fetch_count == 1
        assert first.close_count == second.close_count == 1
        saved = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(saved, snapshot(reopened.ledger.engine))
        with reopened.ledger.engine.connect() as connection:
            assert (
                connection.execute(
                    select(evidence_links.c.evidence_id).where(
                        evidence_links.c.evidence_id == candidates[0].claim_candidate.evidence_id
                    )
                ).scalar_one()
                == candidates[0].claim_candidate.evidence_id
            )
            assert not connection.execute(
                select(evidence_links.c.evidence_id).where(
                    evidence_links.c.evidence_id == candidates[1].claim_candidate.evidence_id
                )
            ).all()
            assert not connection.execute(
                select(evidence_observations.c.observation_id).where(
                    evidence_observations.c.observation_id
                    == candidates[1].observation.observation_id
                )
            ).all()
            statuses = list(
                connection.execute(
                    select(source_bindings.c.state).where(source_bindings.c.project_id == project)
                ).scalars()
            )
            assert statuses.count("ACTIVE") == 2 and statuses.count("DETACHED") == 1
    finally:
        reopened.close()
