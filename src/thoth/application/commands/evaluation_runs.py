"""Public upstream for a configured offline comparison and its immutable result."""

from pydantic import Field, JsonValue

from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.domain.base import DomainModel
from thoth.domain.evaluation_run import EvaluationRunError
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.control_record import ControlRecordStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class EvaluationRunInput(DomainModel):
    project_id: str
    evaluation_plan_id: str
    expected_evaluation_revision: int = Field(ge=1)
    binding_id: str


class EvaluationResultInput(DomainModel):
    project_id: str
    pair_id: str


class EvaluationRunHandlers:
    def __init__(
        self,
        records: ControlRecordStorePort,
        service: PairedEvaluationService | None,
        thread_scope: ImprovementThreadScope,
    ) -> None:
        self._records, self._service = records, service
        self._thread_scope = thread_scope

    async def run(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvaluationRunInput.model_validate(value)
        if self._service is None:
            return {"state": "HELD", "reason_code": "EVALUATOR_NOT_CONFIGURED"}
        plan = self._records.read(request.project_id, "IMPROVEMENT", request.evaluation_plan_id)
        if plan is None or plan.record_type != "EVALUATION_PLAN":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EVALUATION_PLAN_NOT_FOUND")
        self._thread_scope.require_record(plan)
        if plan.version != request.expected_evaluation_revision:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "EVALUATION_PLAN_REVISION_CHANGED"
            )
        try:
            record = await self._service.run(request.project_id, plan, request.binding_id)
        except EvaluationRunError as exc:
            return {"state": "HELD", "reason_code": exc.code}
        except ResourceScopeError:
            raise
        except ValueError:
            return {"state": "HELD", "reason_code": "EVALUATION_INPUT_INVALID"}
        return {"state": record.state, "pair": record.model_dump(mode="json")}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvaluationResultInput.model_validate(value)
        if self._service is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EVALUATOR_NOT_CONFIGURED")
        try:
            record = self._service.read(request.project_id, request.pair_id)
        except EvaluationRunError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, exc.code) from exc
        return {"pair": record.model_dump(mode="json")}
