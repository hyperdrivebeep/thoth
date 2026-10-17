"""Reference inquiry transitions reuse the Criterion owner and its existing UoW."""

import hashlib
from dataclasses import dataclass

from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.reference_grounding import (
    check_measurement_grounding,
    eligible,
    formula_inputs,
)
from thoth.application.services.reference_inquiry_drafts import (
    answered_inquiry,
    calculator_request,
    inquiry_values,
    operator_actor,
    prior_request,
    question_set,
    scenario_deltas,
    scenario_variant,
    selected_calculator,
    source_basis_digest,
)
from thoth.application.services.revision_service import CommitResult
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import head_set_digest
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import (
    ThreadExecutionState,
    ThreadLifecycle,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.reference import (
    CONTEXT_FIELDS,
    ReferenceAnswerRequest,
    ReferenceError,
    ReferenceInquiry,
    ReferenceMappingTrace,
    ReferenceRequest,
    ReferenceScenarioResult,
    criterion_reference_basis,
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
            formula_inputs(current, request.target, request.evaluator_binding_digest)

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
            values = inquiry_values(
                project,
                thread,
                request,
                current,
                prior,
                inquiry_id,
                now,
                self.source_digests(project, request.source_refs),
                actor,
                mapping_trace,
                question_order,
            )
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
            updated = answered_inquiry(
                inquiry, question, request, revision, actor, self._clock, mapping_trace
            )
            return self._persist(current, self._evaluate(updated))

    def _evaluate(self, inquiry: ReferenceInquiry) -> ReferenceInquiry:
        missing = inquiry.target.missing()
        questions = question_set(inquiry, missing, self._ids)
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
                    inputs = formula_inputs(
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
                variant = scenario_variant(inquiry, measurements)
                self._grounded(variant, calculator.minimum_independent_sources)
                calculated = calculator.calculate(variant)
                lower, upper = scenario_deltas(calculated, base)
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
            check_measurement_grounding(inquiry, measurement, span, artifact, spans, target)
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
            formula_inputs(current, inquiry.target, inquiry.evaluator_binding_digest)
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
            actor=operator_actor(current, actor),
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
            eligible(span, artifact)
        return tuple(spans.values())

    def source_digests(self, project: str, refs: tuple[str, ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for span in self.mapping_sources(project, refs):
            artifact = self._artifacts.read_artifact(span.artifact_id)
            if artifact is None:
                raise ReferenceError("REFERENCE_SOURCE_SCOPE_MISMATCH")
            result[span.span_id] = source_basis_digest(span, artifact)
        return result

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
            return prior_request(criterion, prior)
        calculator, version, lane = selected_calculator(thread)
        binding = (criterion.computation_spec or {}).get("evaluator_binding_digest")
        return calculator_request(criterion, lane, calculator, version, binding)
