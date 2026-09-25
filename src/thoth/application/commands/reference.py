"""Attach reference work to the same normal Thread, including literal natural-language replies."""

from pydantic import JsonValue

from thoth.application.commands.thread_analysis import ThreadInputRequest
from thoth.application.services.reference_mapping import ModelReferenceMapper
from thoth.application.services.reference_service import ReferenceService, ReferenceTransition
from thoth.domain.evidence import InformationSufficiencyAssessment
from thoth.domain.reference import ReferenceAnswerRequest, ReferenceError, ReferenceRequest
from thoth.ports.thread import ThreadEntryPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ReferenceThreadEntry:
    def __init__(
        self, delegate: ThreadEntryPort, service: ReferenceService, mapper: ModelReferenceMapper
    ) -> None:
        self._delegate, self._service, self._mapper = delegate, service, mapper

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        self._delegate.authorize_before_claim(method, value)
        request = ThreadInputRequest.model_validate(value)
        if request.reference_request is not None and request.reference_answer is not None:
            raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "REFERENCE_REQUEST_AMBIGUOUS")
        try:
            selected = request.reference_request or request.reference_answer
            if selected is None and request.instruction:
                selected = self._service.automatic_request(request.project_id, request.thread_id)
            if selected is not None:
                self._service.authorize(request.project_id, request.thread_id, selected)
        except ReferenceError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
            ) from exc

    async def analyze(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ThreadInputRequest.model_validate(value)
        try:
            selected = request.reference_request or request.reference_answer
            if selected is not None:
                self._service.current(
                    request.project_id,
                    request.thread_id,
                    selected.criterion_id,
                    selected.expected_revision_digest,
                )
            result = await self._delegate.analyze(value)
            if result.get("queued") is True or "assessment" not in result:
                return result
            transition = await self._reference(request, result)
        except ReferenceError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, exc.code, data={"reason_code": exc.code}
            ) from exc
        if transition is None:
            return result
        return {
            **result,
            "reference_inquiry": transition.inquiry.model_dump(mode="json"),
            "reference_commit": None
            if transition.commit is None
            else transition.commit.model_dump(mode="json"),
            "reference_criterion_revision": transition.criterion.revision_digest,
        }

    async def _reference(
        self, request: ThreadInputRequest, result: dict[str, JsonValue]
    ) -> ReferenceTransition | None:
        project, thread = request.project_id, request.thread_id
        if request.reference_answer is not None:
            return self._service.answer(project, thread, request.reference_answer)
        selected = request.reference_request
        if selected is None:
            if not request.instruction:
                return None
            selected = self._service.automatic_request(project, thread)
        if selected is None:
            return None
        if selected.measurements is not None:
            return self._service.begin(project, thread, selected)
        current = self._service.current(
            project, thread, selected.criterion_id, selected.expected_revision_digest
        )
        spans = self._service.mapping_sources(project, selected.source_refs)
        source_basis = self._service.source_digests(project, selected.source_refs)
        mapped = await self._mapper.map(
            project_id=project,
            thread_id=thread,
            provider=request.provider,
            model=request.model,
            instruction=request.instruction or "Map the selected reference sources.",
            criterion=current,
            target=selected.target,
            evidence=spans,
            assessment=InformationSufficiencyAssessment.model_validate(result["assessment"]),
            prior=current.reference_inquiry,
        )
        self._service.current(
            project, thread, selected.criterion_id, selected.expected_revision_digest
        )
        if self._service.source_digests(project, selected.source_refs) != source_basis:
            raise ReferenceError("REFERENCE_MAPPING_SOURCE_CHANGED")
        prior = current.reference_inquiry
        if prior is not None and mapped.proposal.answers:
            # No await occurs while the grouped answer revisions are committed.
            with self._service.transaction(current):
                transition: ReferenceTransition | None = None
                for candidate in mapped.proposal.answers:
                    question = next(
                        (q for q in prior.questions if q.field == candidate.field), None
                    )
                    if (
                        question is None
                        or not request.instruction
                        or candidate.quote not in request.instruction
                        or candidate.value not in candidate.quote
                    ):
                        raise ReferenceError("REFERENCE_ANSWER_GROUNDING_MISSING")
                    previous = tuple(
                        a for a in prior.answers if a.question_id == question.question_id
                    )
                    response = ReferenceAnswerRequest(
                        criterion_id=current.criterion_id,
                        expected_revision_digest=current.revision_digest,
                        inquiry_id=prior.inquiry_id,
                        question_id=question.question_id,
                        expected_answer_revision=0 if not previous else previous[-1].revision,
                        value=candidate.value,
                    )
                    transition = self._service.answer(
                        project, thread, response, mapping_trace=mapped.trace
                    )
                    current, prior = transition.criterion, transition.inquiry
                return transition
        measurements = mapped.proposal.measurements
        if not measurements and prior is not None:
            return ReferenceTransition(current, prior, None)
        selected = ReferenceRequest.model_validate(
            {
                **selected.model_dump(mode="python"),
                "measurements": measurements,
                "scenarios": mapped.proposal.scenarios or selected.scenarios,
            }
        )
        return self._service.begin(
            project,
            thread,
            selected,
            mapping_trace=mapped.trace,
            question_order=mapped.proposal.missing_field_order,
        )
