"""One typed payload owner; compact model/display views never become source evidence."""

from collections import defaultdict

from thoth.application.services.research_retrieval_policy import retrieval_policy
from thoth.domain.canonical import canonical_payload
from thoth.domain.derived_calculation import (
    NUMERIC_CITATION_INSTRUCTION,
    CheckedObservations,
    DerivedCalculation,
    NumericDelivery,
)
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.model import ContextPack
from thoth.domain.numeric_series import NumericSeriesObservation
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import RevisionRef

NUMERIC_ROLES = frozenset(
    {
        ModelRole.SEMANTIC_REVIEWER,
        ModelRole.REVIEW_ADJUDICATOR,
        ModelRole.HYPOTHESIS_GENERATOR,
    }
)
# Only the roles that write the answer's reading of the numbers are told to cite them.
CITATION_ROLES = frozenset({ModelRole.SEMANTIC_REVIEWER, ModelRole.HYPOTHESIS_GENERATOR})
INSTRUCTION_KEY = "numeric_instruction"
_NUMERIC_KEYS = frozenset({"checked_observations", INSTRUCTION_KEY})


def _in_input(series: NumericSeriesObservation, source: dict[str, EvidenceSpan]) -> bool:
    return all(
        (span := source.get(ref.span_id)) is not None
        and (
            span.project_id,
            span.artifact_id,
            span.source_version_id,
            span.text_sha256,
            span.locator,
        )
        == (
            series.project_id,
            series.artifact_id,
            series.source_version_id,
            ref.text_sha256,
            ref.locator,
        )
        for ref in series.source_refs
    )


def observation_rows(
    observation: CheckedObservations, allowed: set[str] | None = None
) -> list[dict[str, object]]:
    series = {s.binding_digest: s for s in observation.series}
    return [
        {
            "label": series[c.binding_digest].label,
            "context_label": series[c.binding_digest].context_label,
            "size_label": series[c.binding_digest].size_label,
            "sizes": list(c.sizes),
            "operation": c.operation,
            "value": c.value,
            "display_value": c.display_value,
            "unit": c.unit,
            "status": c.status,
            "reason": c.reason,
            "operand_refs": list(c.operand_refs),
            "calculation_ref": c.input_digest,
        }
        for c in observation.calculations
        if allowed is None or c.binding_digest in allowed
    ]


def with_numeric_observations(
    context: ContextPack, role: ModelRole, work: ResearchWork
) -> ContextPack:
    if _NUMERIC_KEYS & context.research_context.keys():
        context = context.model_copy(
            update={
                "research_context": {
                    k: v for k, v in context.research_context.items() if k not in _NUMERIC_KEYS
                }
            }
        )
    raw = work.result_only.get("checked_observations")
    if raw is None or role not in NUMERIC_ROLES:
        return context
    observation = CheckedObservations.model_validate(raw)
    if (
        observation.request_ref != work.request_ref
        or context.project_id != work.request_ref.project_id
    ):
        raise ValueError("NUMERIC_REQUEST_BASIS_MISMATCH")
    source = {s.span_id: s for s in context.evidence}
    allowed = {s.binding_digest for s in observation.series if _in_input(s, source)}
    view, delivered, reasons = _model_view(observation, allowed, context.evidence)
    not_delivered = len(observation.calculations) - delivered
    delivery = NumericDelivery(
        role=role.value, delivered=delivered, not_delivered=not_delivered, reasons=reasons
    )
    deliveries = {d.role: d for d in observation.deliveries}
    deliveries[role.value] = delivery
    work.result_only["checked_observations"] = observation.model_copy(
        update={"deliveries": tuple(deliveries.values())}
    ).model_dump(mode="json")
    if view is None:
        return context
    added: dict[str, object] = {"checked_observations": view}
    if role in CITATION_ROLES:
        added[INSTRUCTION_KEY] = NUMERIC_CITATION_INSTRUCTION
    return context.model_copy(update={"research_context": {**context.research_context, **added}})


def _interval_rows(
    calculations: list[DerivedCalculation], indexes: dict[str, int]
) -> list[tuple[dict[str, object], int]]:
    groups: dict[tuple[str, tuple[str, str], tuple[str, str]], list[DerivedCalculation]] = (
        defaultdict(list)
    )
    for calculation in calculations:
        groups[calculation.binding_digest, calculation.sizes, calculation.operand_refs].append(
            calculation
        )
    rows: list[tuple[dict[str, object], int]] = []
    for (binding, sizes, refs), operations in groups.items():
        row: dict[str, object] = {
            "series_index": indexes[binding],
            "sizes": list(sizes),
            "operand_refs": list(refs),
        }
        for calculation in operations:
            key = calculation.operation.lower()
            if key in row:
                raise ValueError("NUMERIC_INTERVAL_DUPLICATE_OPERATION")
            row[key] = (
                calculation.display_value
                if calculation.status == "CALCULATED"
                else {"status": calculation.status, "reason": calculation.reason}
            )
        rows.append((row, len(operations)))
    return rows


def _model_view(
    observation: CheckedObservations, allowed: set[str], evidence: tuple[EvidenceSpan, ...]
) -> tuple[dict[str, object] | None, int, tuple[str, ...]]:
    eligible = [c for c in observation.calculations if c.binding_digest in allowed]
    source_missing = len(eligible) != len(observation.calculations)
    series = [s for s in observation.series if s.binding_digest in allowed]
    indexes = {s.binding_digest: n for n, s in enumerate(series)}
    rows: list[dict[str, object]] = []
    view: dict[str, object] = {
        "schema_version": "1.0.0",
        "kind": "SOURCE_ARITHMETIC",
        "projection_version": "interval-display:1.0.0",
        "contract": observation.contract,
        "authority": "DERIVED_OBSERVATION_ONLY",
        "precision": 28,
        "rounding": "ROUND_HALF_EVEN",
        "display_decimal_places": 6,
        "not_delivered_count_unit": "calculations",
        "calculator_versions": sorted({c.calculator_version for c in observation.calculations}),
        "grammar_versions": sorted({s.grammar_version for s in series}),
        "unit_registry_digests": sorted({s.unit_registry_digest for s in series}),
        "series": [
            {
                "label": s.label,
                "context_label": s.context_label,
                "size_label": s.size_label,
                "unit": s.unit,
            }
            for s in series
        ],
        "intervals": rows,
        "assumptions": sorted({a for s in series for a in s.assumptions}),
        "diagnostic_reasons": sorted({d.reason for d in observation.diagnostics}),
        "not_delivered": len(observation.calculations),
        "delivery_reasons": ["NUMERIC_SOURCE_NOT_IN_INPUT"] if source_missing else [],
    }
    room = retrieval_policy().character_budget - sum(len(s.exact_text) for s in evidence)
    # Reserve room for the truthful omission notice before admitting any arithmetic rows.
    reasons = ["NUMERIC_SOURCE_NOT_IN_INPUT"] if source_missing else []
    view["delivery_reasons"] = [*reasons, "NUMERIC_POLICY_LIMIT"]
    if len(canonical_payload(view).decode()) > room:
        return None, 0, tuple([*reasons, "NUMERIC_POLICY_LIMIT"])
    delivered = 0
    for row, count in _interval_rows(eligible, indexes):
        rows.append(row)
        if len(canonical_payload(view).decode()) > room:
            rows.pop()
            break
        delivered += count
    if delivered != len(eligible):
        reasons.append("NUMERIC_POLICY_LIMIT")
    view["not_delivered"] = len(observation.calculations) - delivered
    view["delivery_reasons"] = reasons
    return view, delivered, tuple(reasons)


def validate_numeric_publication(
    result: dict[str, object], request: RevisionRef, evidence: tuple[EvidenceSpan, ...]
) -> None:
    raw = result.get("checked_observations")
    if raw is None:
        return
    observation = CheckedObservations.model_validate(raw)
    if observation.request_ref != request:
        raise ValueError("NUMERIC_REQUEST_BASIS_MISMATCH")
    source = {s.span_id: s for s in evidence}
    if any(not _in_input(s, source) for s in observation.series):
        raise ValueError("NUMERIC_PUBLICATION_SOURCE_MISMATCH")


def summarize_checked_observations(raw: object) -> dict[str, object]:
    observation = CheckedObservations.model_validate(raw)
    return {
        "schema_version": observation.schema_version,
        "kind": observation.kind,
        "contract": observation.contract,
        "display_rows": observation_rows(observation),
        "not_calculated": sum(c.status == "NOT_CALCULATED" for c in observation.calculations),
        "diagnostic_count": len(observation.diagnostics),
        "not_delivered": sum(d.not_delivered for d in observation.deliveries),
    }
