"""Reference inquiry transitions reuse the Criterion owner and its existing UoW."""

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast

from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.actor import ActorRef
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import (
    ActorKind,
    AuthorityState,
    CutoffState,
    SupportState,
    ThreadExecutionState,
    ThreadLifecycle,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.reference import (
    CONTEXT_FIELDS,
    ReferenceAnswer,
    ReferenceAnswerRequest,
    ReferenceContext,
    ReferenceError,
    ReferenceInquiry,
    ReferenceMappingTrace,
    ReferenceQuestion,
    ReferenceRequest,
    ReferenceScenarioResult,
    criterion_reference_basis,
    evaluate_reference_formula,
    seal_inquiry,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.reference_calculator import ReferenceCalculatorRegistryPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadStorePort


@dataclass(frozen=True)
class ReferenceTransition:
    criterion: CriterionContractRecord
    inquiry: ReferenceInquiry
    commit: CommitResult | None


class ReferenceService:
    def transaction(self, current: CriterionContractRecord):
        return self._service.transaction(current)

    def __init__(
        self,
        *,
        criteria: CriterionContractStorePort,
        service: CriterionContractService,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        threads: ThreadStorePort,
        calculators: ReferenceCalculatorRegistryPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._criteria, self._service, self._artifacts = criteria, service, artifacts
        self._ledger, self._threads, self._calculators = ledger, threads, calculators
        self._clock, self._ids = clock, ids

    def current(
        self, project: str, thread_id: str, criterion_id: str, expected: str | None
    ) -> CriterionContractRecord:
        thread = self._threads.read(thread_id)
        current = self._criteria.read_contract(project, criterion_id, None)
        if thread is None or thread.project_id != project or current is None:
            raise ReferenceError("REFERENCE_SCOPE_NOT_FOUND")
        if thread.lifecycle != ThreadLifecycle.OPEN or thread.execution_state in {
            ThreadExecutionState.PAUSED,
            ThreadExecutionState.PAUSE_PENDING,
            ThreadExecutionState.CANCELING,
        }:
            raise ReferenceError("REFERENCE_THREAD_INTERRUPTED")
        if current.thread_id is not None and current.thread_id != thread_id:
            raise ReferenceError("REFERENCE_THREAD_SCOPE_MISMATCH")
        if expected is not None and current.revision_digest != expected:
            raise ReferenceError("REFERENCE_CRITERION_REVISION_CONFLICT")
        return current

    def sources(self, project_id: str, refs: tuple[str, ...]) -> dict[str, EvidenceSpan]:
        spans: dict[str, EvidenceSpan] = {}
        for ref in refs:
            span = self._artifacts.read_evidence(ref)
            if span is None or span.project_id != project_id:
                raise ReferenceError("REFERENCE_SOURCE_SCOPE_MISMATCH")
            if hashlib.sha256(span.exact_text.encode()).hexdigest() != span.text_sha256:
                raise ReferenceError("REFERENCE_SOURCE_DIGEST_MISMATCH")
            spans[ref] = span
        return spans

    def authorize(
        self, project: str, thread: str, request: ReferenceRequest | ReferenceAnswerRequest
    ) -> None:
        # The bus checks current access before replaying an idempotent result. The request's
        # old revision is expected on a replay; execution checks it again before any new work.
        current = self.current(project, thread, request.criterion_id, None)
        refs = (
            request.source_refs
            if isinstance(request, ReferenceRequest)
            else (
                () if current.reference_inquiry is None else current.reference_inquiry.source_refs
            )
        )
        self.mapping_sources(project, refs)
        if isinstance(request, ReferenceRequest) and request.lane == "RECALCULATED_VALUE":
            self._formula_inputs(current, request.target, request.evaluator_binding_digest)

    def begin(
        self,
        project: str,
        thread: str,
        request: ReferenceRequest,
        *,
        mapping_trace: ReferenceMappingTrace | None = None,
        question_order: tuple[str, ...] = (),
    ) -> ReferenceTransition:
        current = self.current(
            project, thread, request.criterion_id, request.expected_revision_digest
        )
        with self._service.transaction(current):
            self.sources(project, request.source_refs)
            prior = current.reference_inquiry
            if (
                prior is not None
                and prior.state in {"NEEDS_INPUT", "HOLD"}
                and request.inquiry_id != prior.inquiry_id
            ):
                raise ReferenceError("REFERENCE_INQUIRY_ALREADY_OPEN")
            if request.inquiry_id is not None and (
                prior is None or request.inquiry_id != prior.inquiry_id
            ):
                raise ReferenceError("REFERENCE_INQUIRY_NOT_FOUND")
            if prior is not None and request.inquiry_id:
                if prior.revision >= 128:
                    raise ReferenceError("REFERENCE_INQUIRY_LIMIT")
                if (request.lane, request.calculator_id, request.calculator_version) != (
                    prior.lane,
                    prior.calculator_id,
                    prior.calculator_version,
                ):
                    raise ReferenceError("REFERENCE_NEW_INQUIRY_REQUIRED")
            if prior is not None and request.inquiry_id and prior.thread_id != thread:
                raise ReferenceError("REFERENCE_THREAD_SCOPE_MISMATCH")
            if (
                prior is not None
                and request.inquiry_id
                and prior.criterion_basis_digest
                != criterion_reference_basis(current.model_dump(mode="python"))
            ):
                raise ReferenceError("REFERENCE_NEW_INQUIRY_REQUIRED")
            actor = current_authenticated_actor()
            now = self._clock.now()
            inquiry_id = request.inquiry_id or self._ids.new("reference-inquiry")
            values: dict[str, object] = {
                "inquiry_id": inquiry_id,
                "project_id": project,
                "thread_id": thread,
                "criterion_id": current.criterion_id,
                "criterion_basis_digest": criterion_reference_basis(
                    current.model_dump(mode="python")
                ),
                "original_revision_digest": current.revision_digest
                if not request.inquiry_id or prior is None
                else prior.original_revision_digest,
                "lane": request.lane,
                "revision": 1 if not request.inquiry_id or prior is None else prior.revision + 1,
                "state": "HOLD",
                "source_refs": tuple(dict.fromkeys(request.source_refs)),
                "source_digests": self.source_digests(project, request.source_refs),
                "questions": () if not request.inquiry_id or prior is None else prior.questions,
                "question_order": tuple(f for f in question_order if f in CONTEXT_FIELDS),
                "mapping_traces": self._traces(
                    prior if request.inquiry_id else None, mapping_trace
                ),
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
                "assumptions": (
                    "Declared target context; source mapping remains an auditable candidate.",
                ),
            }
            inquiry = self._evaluate(seal_inquiry(values))
            return self._persist(current, inquiry)

    def answer(
        self,
        project: str,
        thread: str,
        request: ReferenceAnswerRequest,
        *,
        mapping_trace: ReferenceMappingTrace | None = None,
    ) -> ReferenceTransition:
        current = self.current(
            project, thread, request.criterion_id, request.expected_revision_digest
        )
        with self._service.transaction(current):
            inquiry = current.reference_inquiry
            if inquiry is None or inquiry.inquiry_id != request.inquiry_id:
                raise ReferenceError("REFERENCE_INQUIRY_NOT_FOUND")
            if inquiry.revision >= 128 or len(inquiry.answers) >= 128:
                raise ReferenceError("REFERENCE_INQUIRY_LIMIT")
            if inquiry.thread_id != thread:
                raise ReferenceError("REFERENCE_THREAD_SCOPE_MISMATCH")
            if inquiry.criterion_basis_digest != criterion_reference_basis(
                current.model_dump(mode="python")
            ):
                raise ReferenceError("REFERENCE_INQUIRY_BASIS_STALE")
            self.sources(project, inquiry.source_refs)
            if self.source_digests(project, inquiry.source_refs) != inquiry.source_digests:
                raise ReferenceError("REFERENCE_SOURCE_BASIS_CHANGED")
            question = next(
                (q for q in inquiry.questions if q.question_id == request.question_id), None
            )
            if question is None or question.field not in CONTEXT_FIELDS:
                raise ReferenceError("REFERENCE_QUESTION_NOT_FOUND")
            answers = tuple(a for a in inquiry.answers if a.question_id == question.question_id)
            revision = 0 if not answers else answers[-1].revision
            if request.expected_answer_revision != revision:
                raise ReferenceError("REFERENCE_ANSWER_REVISION_CONFLICT")
            actor = current_authenticated_actor()
            answer = ReferenceAnswer(
                question_id=question.question_id,
                field=question.field,
                revision=revision + 1,
                value=request.value,
                actor_ref="local:operator" if actor is None else actor.actor_id,
                session_ref=None if actor is None else actor.session_id,
                answered_at=self._clock.now(),
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
                    "updated_at": self._clock.now(),
                    "mapping_traces": self._traces(inquiry, mapping_trace),
                }
            )
            return self._persist(current, self._evaluate(updated))

    def _evaluate(self, inquiry: ReferenceInquiry) -> ReferenceInquiry:
        missing = inquiry.target.missing()
        existing = {q.field: q for q in inquiry.questions}
        questions = tuple(
            existing.get(field)
            or ReferenceQuestion(
                question_id=self._ids.new("reference-question"),
                field=field,
                basis_revision_digest=inquiry.original_revision_digest,
                prompt=f"Provide the target {field}; no value will be inferred without it.",
            )
            for field in dict.fromkeys(
                (*existing, *(f for f in inquiry.question_order if f in missing), *missing)
            )
        )
        payload = inquiry.model_dump(mode="python", exclude={"inquiry_digest"})
        payload.update(questions=questions, calculation=None, scenario_results=(), reason_codes=())
        if missing:
            payload.update(state="NEEDS_INPUT", reason_codes=("REFERENCE_CONTEXT_MISSING",))
        elif not inquiry.measurements:
            payload.update(state="HOLD", reason_codes=("REFERENCE_MEASUREMENTS_REQUIRED",))
        else:
            try:
                calculator = self._calculators.resolve(
                    inquiry.calculator_id, inquiry.calculator_version
                )
                if calculator.lane != inquiry.lane:
                    raise ReferenceError("REFERENCE_CALCULATOR_LANE_MISMATCH")
                inputs = None
                if inquiry.lane == "RECALCULATED_VALUE":
                    current = self._criteria.read_contract(
                        inquiry.project_id, inquiry.criterion_id, None
                    )
                    if current is None:
                        raise ReferenceError("REFERENCE_SCOPE_NOT_FOUND")
                    inputs = self._formula_inputs(
                        current, inquiry.target, inquiry.evaluator_binding_digest
                    )
                self._grounded(inquiry, calculator.minimum_independent_sources)
                calculation = calculator.calculate(inquiry, inputs)
                payload.update(state="CALCULATED", calculation=calculation)
                if inquiry.scenarios:
                    payload["scenario_results"] = self._scenarios(inquiry)
            except ReferenceError as exc:
                payload.update(state="HOLD", reason_codes=(exc.code,))
        return seal_inquiry(payload)

    def _scenarios(self, inquiry: ReferenceInquiry) -> tuple[ReferenceScenarioResult, ...]:
        calculator = self._calculators.resolve(inquiry.calculator_id, inquiry.calculator_version)
        base = calculator.calculate(inquiry)
        results: list[ReferenceScenarioResult] = []
        for scenario in inquiry.scenarios:
            selected = set(scenario.measurement_span_ids)
            measurements = tuple(m for m in inquiry.measurements if m.span_id in selected)
            try:
                if {m.span_id for m in measurements} != selected:
                    raise ReferenceError("REFERENCE_SCENARIO_SOURCE_NOT_BOUND")
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
                self._grounded(variant, calculator.minimum_independent_sources)
                calculated = calculator.calculate(variant)
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
                results.append(
                    ReferenceScenarioResult(
                        scenario=scenario,
                        state="CALCULATED",
                        calculation=calculated,
                        lower_delta_from_base=lower,
                        upper_delta_from_base=upper,
                    )
                )
            except ReferenceError as exc:
                results.append(
                    ReferenceScenarioResult(
                        scenario=scenario,
                        state="HOLD",
                        reason_codes=(exc.code,),
                    )
                )
        return tuple(results)

    @staticmethod
    def _formula_inputs(
        current: CriterionContractRecord, target: ReferenceContext, binding: str | None
    ) -> dict[str, str]:
        computation = current.computation_spec or {}
        if (
            current.usage_authorization != "AUTHORIZED_EVALUATOR_INPUT"
            or computation.get("type") != "FORMULA"
        ):
            raise ReferenceError("REFERENCE_FORMULA_NOT_AUTHORIZED")
        if binding is None or binding != computation.get("evaluator_binding_digest"):
            raise ReferenceError("REFERENCE_FORMULA_BINDING_MISMATCH")
        if target.formula != computation.get("expression") or target.unit != computation.get(
            "unit"
        ):
            raise ReferenceError("REFERENCE_FORMULA_TARGET_MISMATCH")
        units = computation.get("input_units")
        if not isinstance(units, dict) or not units:
            raise ReferenceError("REFERENCE_FORMULA_INPUT_CONTRACT_MISSING")
        values = cast(dict[object, object], units)
        if any(
            not isinstance(k, str) or not isinstance(v, str) or not k or not v
            for k, v in values.items()
        ):
            raise ReferenceError("REFERENCE_FORMULA_INPUT_CONTRACT_MISSING")
        return {str(k): str(v) for k, v in values.items()}

    @staticmethod
    def _eligible(span: EvidenceSpan, artifact: ArtifactEnvelope) -> None:
        if (
            span.cutoff_state != CutoffState.ELIGIBLE
            or artifact.cutoff_state != CutoffState.ELIGIBLE
        ):
            raise ReferenceError("REFERENCE_SOURCE_CUTOFF_INELIGIBLE")
        if span.authority_state not in {
            AuthorityState.INFORMAL,
            AuthorityState.OFFICIAL,
            AuthorityState.APPROVED,
        }:
            raise ReferenceError("REFERENCE_SOURCE_AUTHORITY_UNRESOLVED")
        if span.support_state not in {
            SupportState.EXTRACTED,
            SupportState.SUPPORTED_CANDIDATE,
            SupportState.SUPPORTED,
        }:
            raise ReferenceError("REFERENCE_SOURCE_SUPPORT_UNRESOLVED")
        if span.verification_state not in {
            VerificationState.SCHEMA_VALID,
            VerificationState.PROVENANCE_VALID,
            VerificationState.DETERMINISTICALLY_VERIFIED,
        }:
            raise ReferenceError("REFERENCE_SOURCE_PROVENANCE_UNRESOLVED")

    def _grounded(self, inquiry: ReferenceInquiry, minimum_sources: int) -> None:
        spans = self.sources(inquiry.project_id, inquiry.source_refs)
        uris: set[str] = set()
        hashes: set[str] = set()
        target = inquiry.target.model_dump()
        for measurement in inquiry.measurements:
            span = spans.get(measurement.span_id)
            if span is None:
                raise ReferenceError("REFERENCE_MEASUREMENT_SOURCE_NOT_BOUND")
            artifact = self._artifacts.read_artifact(span.artifact_id)
            if artifact is None or artifact.project_id != inquiry.project_id:
                raise ReferenceError("REFERENCE_SOURCE_SCOPE_MISMATCH")
            # Extracted values may form a non-authoritative range after grounding checks;
            # this never promotes the source or a scientific claim to SUPPORTED.
            self._eligible(span, artifact)
            fields = measurement.context.model_dump()
            for field in CONTEXT_FIELDS:
                text = fields[field]
                quote = measurement.quotes.get(field)
                field_span = spans.get(measurement.field_span_ids.get(field, measurement.span_id))
                if (
                    field_span is None
                    or field_span.artifact_id != span.artifact_id
                    or not text
                    or not quote
                    or quote not in field_span.exact_text
                    or text not in quote
                ):
                    raise ReferenceError("REFERENCE_FIELD_GROUNDING_MISSING")
                self._eligible(field_span, artifact)
                if (
                    field != "unit"
                    and text.strip().casefold() != str(target[field]).strip().casefold()
                ):
                    raise ReferenceError("REFERENCE_CONTEXT_INCOMPATIBLE")
            if measurement.value_quote not in span.exact_text:
                raise ReferenceError("REFERENCE_VALUE_GROUNDING_MISSING")
            numbers = re.findall(
                r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![\w.])",
                measurement.value_quote,
            )
            if {Decimal(n) for n in numbers} != {measurement.value}:
                raise ReferenceError("REFERENCE_VALUE_GROUNDING_MISSING")
            if inquiry.lane == "RECALCULATED_VALUE" and (
                measurement.variable is None
                or re.search(
                    r"(?<!\w)" + re.escape(measurement.variable) + r"(?!\w)",
                    measurement.value_quote,
                )
                is None
            ):
                raise ReferenceError("REFERENCE_VARIABLE_GROUNDING_MISSING")
            uris.add(artifact.source_uri)
            hashes.add(artifact.byte_sha256)
        if len(uris) < minimum_sources or len(hashes) < minimum_sources:
            raise ReferenceError("REFERENCE_INDEPENDENT_SOURCES_REQUIRED")

    def _persist(
        self, current: CriterionContractRecord, inquiry: ReferenceInquiry
    ) -> ReferenceTransition:
        # Reference ranges never update the official result; authorized recalculation is separate.
        actor = current_authenticated_actor()
        updates: dict[str, object] = {"reference_inquiry": inquiry, "schema_version": "1.1.0"}
        if inquiry.lane == "RECALCULATED_VALUE" and inquiry.calculation is not None:
            self._formula_inputs(current, inquiry.target, inquiry.evaluator_binding_digest)
            updates["result_and_uncertainty"] = {
                "value": inquiry.calculation.value,
                "variables": inquiry.calculation.variables,
                "evaluator_binding_digest": inquiry.evaluator_binding_digest,
                "calculation_trace_digest": inquiry.calculation.output_digest,
                "measurement_validity": "NOT_ASSESSED",
                "disposition": "NOT_DECIDED",
            }
        updated, commit = self._service.revise(
            current,
            updates=updates,
            event_type="criteria/referenceInquiryUpdated",
            actor=ActorRef(
                actor_id="local:operator" if actor is None else actor.actor_id,
                kind=ActorKind.HUMAN,
                role="local-operator" if actor is None else actor.role,
                project_id=current.project_id,
                session_id=None if actor is None else actor.session_id,
                role_assignment_ref=None if actor is None else actor.role_assignment_id,
            ),
        )
        thread = self._threads.read(inquiry.thread_id)
        if thread is None or not self._threads.update(
            thread.model_copy(
                update={
                    "working_head_digest": head_set_digest(
                        self._ledger.read_heads(current.project_id)
                    ),
                    "revision": thread.revision + 1,
                    "updated_at": self._clock.now(),
                }
            ),
            expected_revision=thread.revision,
        ):
            raise ReferenceError("REFERENCE_THREAD_REVISION_CONFLICT")
        self.mapping_sources(current.project_id, inquiry.source_refs)
        return ReferenceTransition(updated, inquiry, commit)

    def mapping_sources(self, project_id: str, refs: tuple[str, ...]) -> tuple[EvidenceSpan, ...]:
        spans = self.sources(project_id, refs)
        for span in spans.values():
            artifact = self._artifacts.read_artifact(span.artifact_id)
            if artifact is None or artifact.project_id != project_id:
                raise ReferenceError("REFERENCE_SOURCE_SCOPE_MISMATCH")
            self._eligible(span, artifact)
        return tuple(spans.values())

    def source_digests(self, project: str, refs: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for span in self.mapping_sources(project, refs):
            artifact = self._artifacts.read_artifact(span.artifact_id)
            if artifact is None:
                raise ReferenceError("REFERENCE_SOURCE_SCOPE_MISMATCH")
            result[span.span_id] = domain_digest(
                "REFERENCE_SOURCE_BASIS",
                "1.0.0",
                canonical_payload(
                    {
                        "span": span,
                        "artifact": artifact,
                    }
                ),
            )
        return result

    @staticmethod
    def _traces(
        inquiry: ReferenceInquiry | None, trace: ReferenceMappingTrace | None
    ) -> tuple[ReferenceMappingTrace, ...]:
        prior = () if inquiry is None else inquiry.mapping_traces
        return prior if trace is None or trace in prior else (*prior, trace)

    def automatic_request(self, project_id: str, thread_id: str) -> ReferenceRequest | None:
        thread = self._threads.read(thread_id)
        if thread is None or thread.project_id != project_id:
            return None
        identifier = thread.scope.get("reference_criterion_id")
        if identifier is None:
            pending = tuple(
                c
                for c in self._criteria.list_contracts(project_id)
                if c.reference_inquiry is not None
                and c.reference_inquiry.thread_id == thread_id
                and c.reference_inquiry.state in {"NEEDS_INPUT", "HOLD"}
            )
            if len(pending) > 1:
                raise ReferenceError("REFERENCE_CRITERION_SELECTION_REQUIRED")
            if not pending:
                return None
            criterion = pending[0]
        else:
            criterion = self._criteria.read_contract(project_id, identifier, None)
            if criterion is None:
                raise ReferenceError("REFERENCE_SCOPE_NOT_FOUND")
        prior = criterion.reference_inquiry
        if prior is not None:
            if prior.criterion_basis_digest != criterion_reference_basis(
                criterion.model_dump(mode="python")
            ):
                raise ReferenceError("REFERENCE_INQUIRY_BASIS_STALE")
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
        selected = thread.scope.get("reference_calculator", "")
        calculator, separator, version = selected.partition("@")
        if not separator or not calculator or not version:
            raise ReferenceError("REFERENCE_CALCULATOR_SELECTION_REQUIRED")
        lane = thread.scope.get("reference_lane", "REFERENCE_RANGE_CANDIDATE")
        if lane not in {"REFERENCE_RANGE_CANDIDATE", "RECALCULATED_VALUE"}:
            raise ReferenceError("REFERENCE_LANE_INVALID")
        binding = (criterion.computation_spec or {}).get("evaluator_binding_digest")
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
