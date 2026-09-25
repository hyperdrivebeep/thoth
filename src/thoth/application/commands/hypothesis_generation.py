"""Atomic persistence of one public hypothesis-generation batch."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.services.hypothesis_service import PRIMARY_INTENTS, HypothesisService
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def generate_hypotheses(
    *,
    service: HypothesisService,
    artifacts: ArtifactLedgerPort,
    project_id: str,
    object_id: str,
    portfolio_id: str | None,
    question: str,
    evidence_scope: tuple[str, ...],
    intent_hints: tuple[str, ...],
    generation_policy_ref: str | None,
    budget_policy_ref: str | None,
) -> dict[str, JsonValue]:
    spans = tuple(
        span
        for reference in evidence_scope
        if (span := artifacts.read_evidence(reference)) is not None
        and span.project_id == project_id
    )
    if len(spans) != len(evidence_scope):
        raise RpcApplicationError(
            RpcErrorCode.DOMAIN_REJECTED,
            "generation evidence scope contains unavailable spans",
        )
    portfolio_id = portfolio_id or f"portfolio:{object_id}"
    primary = next(
        (item for item in intent_hints if item in PRIMARY_INTENTS),
        "DIAGNOSTIC_CAUSAL",
    )
    statements = [
        (
            f"A condition represented by evidence {span.span_id} may contribute to "
            f"the observed problem: {question}"
        )
        for span in spans[:2]
    ]
    statements.append(
        "An unobserved or currently unclassified condition may explain the remaining gap"
    )
    records: list[HypothesisRecord] = []
    commits: list[JsonValue] = []
    duplicates: dict[str, list[str]] = {}
    with service.transaction():
        for index, statement in enumerate(statements):
            intent = "EXPLORATORY" if index == len(statements) - 1 else primary
            try:
                record, duplicate_refs, commit = service.create(
                    project_id=project_id,
                    object_id=object_id,
                    portfolio_id=portfolio_id,
                    statement=statement,
                    primary_intent=intent,
                    secondary_intents=(),
                    evidence_basis=(
                        "UNKNOWN_RESERVE"
                        if intent == "EXPLORATORY"
                        else "SOURCE_GROUNDED_GENERATION"
                    ),
                    scope={"question": question[:1_000]},
                    evidence_refs=(() if intent == "EXPLORATORY" else (spans[index].span_id,)),
                    prespecification_state="UNKNOWN",
                )
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            records.append(record)
            commits.append(cast(JsonValue, commit.model_dump(mode="json")))
            duplicates[record.hypothesis_id] = list(duplicate_refs)
    return cast(
        dict[str, JsonValue],
        {
            "hypotheses": [record.model_dump(mode="json") for record in records],
            "alternative_families": sorted(
                {record.primary_intent for record in records if record.primary_intent}
            ),
            "unknown_reserve": records[-1].hypothesis_id,
            "source_basis": list(evidence_scope),
            "duplicate_candidates": cast(JsonValue, duplicates),
            "generation_policy_ref": generation_policy_ref,
            "budget_policy_ref": budget_policy_ref,
            "commits": commits,
        },
    )
