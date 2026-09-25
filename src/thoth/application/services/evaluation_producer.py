"""Select an explicit runnable evaluation plan for the triggering Thread."""

from dataclasses import dataclass

from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import EvaluationRunError, PairedRunRecord
from thoth.ports.control_record import ControlRecordStorePort


@dataclass(frozen=True)
class SelectedEvaluation:
    plan: ControlRecord
    binding_id: str


class EvaluationProducer:
    def __init__(self, records: ControlRecordStorePort, service: PairedEvaluationService) -> None:
        self._records, self._service = records, service

    def select(self, project_id: str, thread_id: str) -> SelectedEvaluation | None:
        matches: list[SelectedEvaluation] = []
        for plan in self._records.list(project_id, "IMPROVEMENT", "EVALUATION_PLAN"):
            if plan.state != "PLANNED":
                continue
            candidate = self._records.read(
                project_id, "IMPROVEMENT", str(plan.payload.get("improvement_revision_id"))
            )
            if (
                candidate is None
                or candidate.state != "PROPOSED"
                or candidate.payload.get("thread_id") != thread_id
            ):
                continue
            binding_id = candidate.payload.get("evaluation_contract_ref")
            if not isinstance(binding_id, str):
                raise EvaluationRunError("EVALUATION_BINDING_NOT_CONFIGURED")
            matches.append(SelectedEvaluation(plan, binding_id))
        if len(matches) > 1:
            raise EvaluationRunError("EVALUATION_PLAN_AMBIGUOUS")
        return None if not matches else matches[0]

    async def run(self, selected: SelectedEvaluation) -> PairedRunRecord:
        return await self._service.run(selected.plan.project_id, selected.plan, selected.binding_id)
