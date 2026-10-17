"""Construct counter-search intent records and publish accepted evidence."""

from typing import TypedDict

from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.domain.acquisition import AcquisitionLead, SearchIntent
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.counterevidence import CounterReviewTerminal, CounterSearchPlan
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.project import WorkThread
from thoth.ports.acquisition import EvidenceUnitOfWorkPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class CounterEvidenceArguments(TypedDict, total=True):
    project_id: str
    thread: WorkThread
    plan: CounterSearchPlan
    intent: SearchIntent
    source: EvidenceSourceRecord
    spans: tuple[EvidenceSpan, ...]
    terminal: CounterReviewTerminal


def build_counter_search_intent(
    plan: CounterSearchPlan,
    *,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> SearchIntent:
    draft: dict[str, object] = {
        "search_intent_id": ids.new("search-intent"),
        "project_id": plan.project_id,
        "thread_id": plan.thread_id,
        "investigation_id": plan.investigation_id,
        "evidence_group": f"counterevidence:{plan.hypothesis_id}",
        "query_families": plan.query_families,
        "connector_id": plan.connector_id,
        "selector": plan.selector,
        "max_waves": plan.max_waves,
        "max_results": plan.max_results,
        "stop_conditions": (
            "INDEPENDENT_GATE_TERMINAL",
            "SEARCH_SATURATED",
            "POLICY_BLOCKED",
            "BUDGET_EXHAUSTED",
        ),
        "policy_id": plan.policy_id,
        "policy_revision": plan.policy_revision,
        "policy_digest": plan.policy_digest,
        "state": "READY",
        "created_at": clock.now(),
    }
    return SearchIntent.model_validate(
        {
            **draft,
            "intent_digest": domain_digest(
                "COUNTER_SEARCH_INTENT", "1.0.0", canonical_payload(draft)
            ),
        }
    )


def publish_counter_evidence(
    *,
    evidence_graph: EvidenceGraphService,
    evidence_unit_of_work: EvidenceUnitOfWorkPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    project_id: str,
    thread: WorkThread,
    plan: CounterSearchPlan,
    intent: SearchIntent,
    source: EvidenceSourceRecord,
    spans: tuple[EvidenceSpan, ...],
    terminal: CounterReviewTerminal,
) -> None:
    if not spans:
        raise ValueError("gate accepted no evidence spans")
    span = spans[0]
    lead_draft: dict[str, object] = {
        "lead_id": ids.new("counterevidence-lead"),
        "project_id": project_id,
        "investigation_id": plan.investigation_id,
        "search_intent_id": intent.search_intent_id,
        "source_id": source.source_id,
        "span_id": span.span_id,
        "evidence_group": f"counterevidence:{plan.hypothesis_id}",
        "state": "CLAIM_CANDIDATE",
        "information_value": "CRITICAL_COUNTEREVIDENCE",
        "authority_basis": "independent deterministic gate pass",
        "created_at": clock.now(),
    }
    lead = AcquisitionLead.model_validate(
        {
            **lead_draft,
            "lead_digest": domain_digest(
                "COUNTEREVIDENCE_LEAD", "1.0.0", canonical_payload(lead_draft)
            ),
        }
    )
    staged = evidence_graph.stage_link(
        project_id=project_id,
        target_type="HYPOTHESIS",
        target_id=plan.hypothesis_id,
        relation=(
            "CONTRADICTS"
            if terminal == CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE
            else "SUPPORTS"
        ),
        span_ids=tuple(item.span_id for item in spans),
        observed_statement="\n".join(item.exact_text for item in spans),
        thread_id=thread.thread_id,
        conditions={
            "source_territory": plan.source_territory,
            "alternative_explanation": plan.alternative_explanation_track,
        },
        applicability="INDEPENDENT_CRITICAL_REVIEW_CANDIDATE",
        independence_group=plan.independence_group,
        lead=lead,
    )
    evidence_unit_of_work.commit(staged)
