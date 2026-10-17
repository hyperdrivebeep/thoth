"""Pure record construction for Reference inquiries and requests (97).

These functions only turn values the service has already read and checked into inquiry payloads,
answers, question sets, scenario variants, actor references and requests. They read nothing, write
nothing and never see a store, a ledger, a thread store or a calculator. The id generator and clock
ports are handed in and called in the order and number the service called them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from thoth.domain.actor import ActorRef
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import ActorKind
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.project import WorkThread
from thoth.domain.reference import (
    CONTEXT_FIELDS,
    ReferenceAnswer,
    ReferenceAnswerRequest,
    ReferenceCalculation,
    ReferenceContext,
    ReferenceError,
    ReferenceInquiry,
    ReferenceMappingTrace,
    ReferenceMeasurement,
    ReferenceQuestion,
    ReferenceRequest,
    criterion_reference_basis,
    evaluate_reference_formula,
    seal_inquiry,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def inquiry_values(
    project: str,
    thread: str,
    request: ReferenceRequest,
    current: CriterionContractRecord,
    prior: ReferenceInquiry | None,
    inquiry_id: str,
    now: datetime,
    source_digests: dict[str, str],
    actor: AuthenticatedActorContext | None,
    mapping_trace: ReferenceMappingTrace | None,
    question_order: tuple[str, ...],
) -> dict[str, object]:
    values: dict[str, object] = {
        "inquiry_id": inquiry_id,
        "project_id": project,
        "thread_id": thread,
        "criterion_id": current.criterion_id,
        "criterion_basis_digest": criterion_reference_basis(current.model_dump(mode="python")),
        "original_revision_digest": current.revision_digest
        if not request.inquiry_id or prior is None
        else prior.original_revision_digest,
        "lane": request.lane,
        "revision": 1 if not request.inquiry_id or prior is None else prior.revision + 1,
        "state": "HOLD",
        "source_refs": tuple(dict.fromkeys(request.source_refs)),
        "source_digests": source_digests,
        "questions": () if not request.inquiry_id or prior is None else prior.questions,
        "question_order": tuple(f for f in question_order if f in CONTEXT_FIELDS),
        "mapping_traces": merged_traces(prior if request.inquiry_id else None, mapping_trace),
        "calculator_id": request.calculator_id,
        "calculator_version": request.calculator_version,
        "evaluator_binding_digest": request.evaluator_binding_digest,
        "target": request.target,
        "measurements": request.measurements or (),
        "scenarios": request.scenarios,
        "answers": () if not request.inquiry_id or prior is None else prior.answers,
        "actor_ref": "local:operator" if actor is None else actor.actor_id,
        "session_ref": None if actor is None else actor.session_id,
        "created_at": now if not request.inquiry_id or prior is None else prior.created_at,
        "updated_at": now,
        "assumptions": ("Declared target context; source mapping remains an auditable candidate.",),
    }
    return values


def answered_inquiry(
    inquiry: ReferenceInquiry,
    question: ReferenceQuestion,
    request: ReferenceAnswerRequest,
    revision: int,
    actor: AuthenticatedActorContext | None,
    clock: ClockPort,
    mapping_trace: ReferenceMappingTrace | None,
) -> ReferenceInquiry:
    answer = ReferenceAnswer(
        question_id=question.question_id,
        field=question.field,
        revision=revision + 1,
        value=request.value,
        actor_ref="local:operator" if actor is None else actor.actor_id,
        session_ref=None if actor is None else actor.session_id,
        answered_at=clock.now(),
    )
    target = ReferenceContext.model_validate(
        {**inquiry.target.model_dump(), question.field: request.value}
    )
    updated = seal_inquiry(
        {
            **inquiry.model_dump(mode="python", exclude={"inquiry_digest"}),
            "revision": inquiry.revision + 1,
            "target": target,
            "answers": (*inquiry.answers, answer),
            "state": "HOLD",
            "calculation": None,
            "updated_at": clock.now(),
            "mapping_traces": merged_traces(inquiry, mapping_trace),
        }
    )
    return updated


def question_set(
    inquiry: ReferenceInquiry, missing: tuple[str, ...], id_generator: IdGeneratorPort
) -> tuple[ReferenceQuestion, ...]:
    existing = {q.field: q for q in inquiry.questions}
    questions = tuple(
        existing.get(field)
        or ReferenceQuestion(
            question_id=id_generator.new("reference-question"),
            field=field,
            basis_revision_digest=inquiry.original_revision_digest,
            prompt=f"Provide the target {field}; no value will be inferred without it.",
        )
        for field in dict.fromkeys(
            (*existing, *(f for f in inquiry.question_order if f in missing), *missing)
        )
    )
    return questions


def scenario_variant(
    inquiry: ReferenceInquiry, measurements: tuple[ReferenceMeasurement, ...]
) -> ReferenceInquiry:
    variant = seal_inquiry(
        {
            **inquiry.model_dump(mode="python", exclude={"inquiry_digest"}),
            "measurements": measurements,
            "scenarios": (),
            "scenario_results": (),
            "state": "HOLD",
            "calculation": None,
        }
    )
    return variant


def scenario_deltas(
    calculated: ReferenceCalculation, base: ReferenceCalculation
) -> tuple[str | None, str | None]:
    lower = upper = None
    if calculated.lower is not None and base.lower is not None:
        lower = str(
            evaluate_reference_formula(
                "a-b",
                {
                    "a": Decimal(calculated.lower),
                    "b": Decimal(base.lower),
                },
            )
        )
    if calculated.upper is not None and base.upper is not None:
        upper = str(
            evaluate_reference_formula(
                "a-b",
                {
                    "a": Decimal(calculated.upper),
                    "b": Decimal(base.upper),
                },
            )
        )
    return lower, upper


def operator_actor(
    current: CriterionContractRecord, actor: AuthenticatedActorContext | None
) -> ActorRef:
    return ActorRef(
        actor_id="local:operator" if actor is None else actor.actor_id,
        kind=ActorKind.HUMAN,
        role="local-operator" if actor is None else actor.role,
        project_id=current.project_id,
        session_id=None if actor is None else actor.session_id,
        role_assignment_ref=None if actor is None else actor.role_assignment_id,
    )


def source_basis_digest(span: EvidenceSpan, artifact: ArtifactEnvelope) -> str:
    return domain_digest(
        "REFERENCE_SOURCE_BASIS",
        "1.0.0",
        canonical_payload(
            {
                "span": span,
                "artifact": artifact,
            }
        ),
    )


def merged_traces(
    inquiry: ReferenceInquiry | None, trace: ReferenceMappingTrace | None
) -> tuple[ReferenceMappingTrace, ...]:
    prior = () if inquiry is None else inquiry.mapping_traces
    return prior if trace is None or trace in prior else (*prior, trace)


def prior_request(criterion: CriterionContractRecord, prior: ReferenceInquiry) -> ReferenceRequest:
    return ReferenceRequest(
        criterion_id=criterion.criterion_id,
        expected_revision_digest=criterion.revision_digest,
        lane=prior.lane,
        source_refs=prior.source_refs,
        calculator_id=prior.calculator_id,
        calculator_version=prior.calculator_version,
        target=prior.target,
        inquiry_id=prior.inquiry_id,
        evaluator_binding_digest=prior.evaluator_binding_digest,
        scenarios=prior.scenarios,
    )


def selected_calculator(thread: WorkThread) -> tuple[str, str, str]:
    selected = thread.scope.get("reference_calculator", "")
    calculator, separator, version = selected.partition("@")
    if not separator or not calculator or not version:
        raise ReferenceError("REFERENCE_CALCULATOR_SELECTION_REQUIRED")
    lane = thread.scope.get("reference_lane", "REFERENCE_RANGE_CANDIDATE")
    if lane not in {"REFERENCE_RANGE_CANDIDATE", "RECALCULATED_VALUE"}:
        raise ReferenceError("REFERENCE_LANE_INVALID")
    return calculator, version, lane


def calculator_request(
    criterion: CriterionContractRecord,
    lane: str,
    calculator: str,
    version: str,
    binding: object,
) -> ReferenceRequest:
    return ReferenceRequest(
        criterion_id=criterion.criterion_id,
        expected_revision_digest=criterion.revision_digest,
        lane=cast(Literal["REFERENCE_RANGE_CANDIDATE", "RECALCULATED_VALUE"], lane),
        source_refs=criterion.required_evidence,
        calculator_id=calculator,
        calculator_version=version,
        target=ReferenceContext.model_validate(
            {k: v for k, v in criterion.context_spec.items() if k in CONTEXT_FIELDS}
        ),
        evaluator_binding_digest=binding if isinstance(binding, str) else None,
    )
