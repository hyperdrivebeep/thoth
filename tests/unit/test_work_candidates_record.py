"""S4: the selection a request records says what the one selection function chose and why."""

from typing import Literal, cast

import pytest
from tests.unit.test_retrieval_policy_v3_research_path import snapshot
from tests.unit.test_retrieval_selection_oracle import Scenario, build_scenarios

from thoth.application.services import research_retrieval
from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.research_retrieval import work_candidates
from thoth.application.services.research_selection_context import CandidateContext
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.research_execution import ResearchBoundary, ResearchWork
from thoth.domain.research_request import RevisionRef
from thoth.domain.retrieval_lane import full_catalog
from thoth.ports.artifact_ledger import ArtifactLedgerPort

SCENARIOS = build_scenarios()


def provide_context(patch: pytest.MonkeyPatch, scenario: Scenario) -> ArtifactLedgerPort | None:
    """Serve the scenario's observation instead of reading a stored structure."""
    context = scenario.context
    if context is None:
        return None

    def candidate_context(
        evidence: tuple[EvidenceSpan, ...], artifacts: ArtifactLedgerPort
    ) -> CandidateContext:
        return context

    patch.setattr(research_retrieval, "candidate_context", candidate_context)
    return cast(ArtifactLedgerPort, object())


def request_work(scenario: Scenario) -> ResearchWork:
    return ResearchWork(
        RevisionRef(
            project_id="project:s4",
            entity_type="THREAD",
            entity_id="request:1",
            revision_id="revision:1",
            revision_digest="a" * 64,
        ),
        scenario.question,
        cast(ResearchBoundary, object()),
        pinned_spans=scenario.pinned,
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_recorded_selection_follows_the_selection_rules(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = SCENARIOS[name]
    artifacts = provide_context(monkeypatch, scenario)
    work = request_work(scenario)
    with behavior_work_scope(BehaviorWorkContext("p", (snapshot(scenario.policy),))):
        result = work_candidates(work, scenario.queries, scenario.evidence, artifacts)

    # What the rules say, stated apart from the code under test.
    eligible = connected_retrieval_spans(scenario.evidence)
    context = scenario.context
    complete, refusal = full_catalog(
        eligible, None if context is None else context.to_catalog(), scenario.policy
    )
    mode: Literal["FULL_ELIGIBLE_EXTRACTED_CATALOG", "LEXICAL", "LEGACY"] = (
        "FULL_ELIGIBLE_EXTRACTED_CATALOG"
        if complete is not None
        else "LEGACY"
        if context is None or not context.all_observed
        else "LEXICAL"
    )
    selection = work.retrieval_selection
    assert selection is not None
    assert selection.mode == mode
    assert selection.limitations == (
        ("LEGACY_CONTEXT",) if context is None else (*context.limitations, *refusal)
    )
    assert selection.candidate_refs == tuple(s.span_id for s in result)
    assert selection.eligible == eligible and selection.catalog == scenario.evidence
    assert selection.policy_digest == domain_digest(
        "RETRIEVAL_POLICY", "1.0.0", canonical_payload(scenario.policy)
    )
    assert selection.contextual_refs == (() if context is None else context.ordered_refs)
    assert selection.source_basis_digest == (
        domain_digest("RETRIEVAL_SOURCE", "1.0.0", canonical_payload({"source": scenario.evidence}))
        if context is None
        else context.basis_digest
    )


def test_the_scenarios_cover_every_recorded_mode_and_a_refusal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes: set[str] = set()
    limitations: set[str] = set()
    for scenario in SCENARIOS.values():
        artifacts = provide_context(monkeypatch, scenario)
        work = request_work(scenario)
        with behavior_work_scope(BehaviorWorkContext("p", (snapshot(scenario.policy),))):
            work_candidates(work, scenario.queries, scenario.evidence, artifacts)
        assert work.retrieval_selection is not None
        modes.add(work.retrieval_selection.mode)
        limitations.update(work.retrieval_selection.limitations)
    assert modes == {"FULL_ELIGIBLE_EXTRACTED_CATALOG", "LEXICAL", "LEGACY"}
    assert {"LEGACY_CONTEXT", "CONTEXT_ORDERED_REFS_MISMATCH"} <= limitations
