"""Portfolio field calculations and drafts, without store or ledger access."""

from __future__ import annotations

from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def portfolio_creation_draft(
    *,
    project_id: str,
    object_id: str,
    effective_id: str,
    hypothesis_ids: tuple[str, ...],
    unknown_reserve: dict[str, object],
    hypotheses: tuple[HypothesisRecord, ...],
    current: HypothesisPortfolioRecord | None,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    statements = {item.statement.casefold() for item in hypotheses}
    quality_gaps = tuple(
        gap
        for gap, present in (
            (
                "semantic diversity",
                len(hypotheses) > 1 and len(statements) == len(hypotheses),
            ),
            (
                "counterevidence route",
                bool(hypotheses) and all(item.counterevidence_queries for item in hypotheses),
            ),
            (
                "prediction coverage",
                bool(hypotheses) and all(item.prediction_refs for item in hypotheses),
            ),
        )
        if not present
    )
    draft: dict[str, object] = {
        "portfolio_revision_id": ids.new("portfolio-revision"),
        "portfolio_id": effective_id,
        "project_id": project_id,
        "object_id": object_id,
        "hypothesis_refs": hypothesis_ids,
        "relation_refs": (() if current is None else current.relation_refs),
        "unknown_reserve": unknown_reserve,
        "coverage_state": "NOT_ASSESSED"
        if not hypotheses
        else "PARTIAL"
        if quality_gaps
        else "COVERED",
        "diversity_state": (
            "NOT_ASSESSED"
            if len(hypotheses) < 2
            else "DIVERSE"
            if len(statements) == len(hypotheses)
            else "PARAPHRASE_RISK"
        ),
        "discrimination_state": (
            "NOT_ASSESSED"
            if not hypotheses
            else "CANDIDATE"
            if all(item.prediction_refs for item in hypotheses)
            else "MISSING"
        ),
        "abstention_state": "AVAILABLE",
        "quality_gaps": quality_gaps,
        "discrimination_matrix": (),
        "supersedes_revision_digest": (None if current is None else current.revision_digest),
        "created_at": clock.now(),
    }
    return draft


def portfolio_revision_draft(
    current: HypothesisPortfolioRecord,
    *,
    updates: dict[str, object],
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "portfolio_revision_id": ids.new("portfolio-revision"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    draft.pop("receipt_ref", None)
    return draft
