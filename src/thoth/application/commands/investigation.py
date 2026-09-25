from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.base import DomainModel
from thoth.domain.investigation import InvestigationRecord
from thoth.ports.acquisition import AcquisitionTraceStorePort
from thoth.ports.investigation import InvestigationStorePort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class InvestigationListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str | None = Field(default=None, max_length=160)


class InvestigationReadInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    investigation_id: str = Field(min_length=1, max_length=160)


class InvestigationAuditInput(InvestigationReadInput):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1_000)


class InvestigationStartInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str = Field(min_length=1, max_length=160)
    trigger: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=20_000)
    parent_investigation_id: str | None = Field(default=None, max_length=160)
    target_object_id: str | None = Field(default=None, max_length=160)
    target_hypothesis_id: str | None = Field(default=None, max_length=160)
    mode: str = Field(default="BOUNDED", pattern=r"^(BOUNDED|CRITICAL|SATURATION)$")
    scope: dict[str, str] = Field(default_factory=dict)
    required_evidence_groups: tuple[str, ...] = ()
    query_families: tuple[str, ...] = ()
    budget: int = Field(default=30, ge=1, le=1_000_000)
    stop_conditions: tuple[str, ...] = ()
    explicit_saturation_opt_in: bool = False


class InvestigationUpdateInput(InvestigationReadInput):
    expected_plan_revision: int = Field(ge=0)
    mode: str | None = Field(default=None, pattern=r"^(BOUNDED|CRITICAL|SATURATION)$")
    scope: dict[str, str] | None = None
    required_evidence_groups: tuple[str, ...] | None = None
    query_families: tuple[str, ...] | None = None
    budget_extension: int = Field(default=0, ge=0, le=1_000_000)
    stop_conditions: tuple[str, ...] | None = None
    explicit_saturation_opt_in: bool = False
    reason: str = Field(min_length=1, max_length=2_000)


class InvestigationResumeInput(InvestigationReadInput):
    expected_checkpoint_digest: str = Field(min_length=64, max_length=64)


class InvestigationStopInput(InvestigationReadInput):
    reason: str = Field(min_length=1, max_length=80)


class InvestigationQueryHandlers:
    def __init__(
        self,
        *,
        store: InvestigationStorePort,
        service: InvestigationService,
        threads: ThreadStorePort,
        acquisition: AcquisitionTraceStorePort,
    ) -> None:
        self._store = store
        self._service = service
        self._threads = threads
        self._acquisition = acquisition

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationListInput.model_validate(value)
        return {
            "investigations": [
                item.model_dump(mode="json")
                for item in self._store.list(request.project_id, request.thread_id)
            ]
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationReadInput.model_validate(value)
        investigation = self._read(request)
        return {
            "investigation": investigation.model_dump(mode="json"),
            "search_intents": [
                item.model_dump(mode="json")
                for item in self._acquisition.list_search_intents(
                    investigation.investigation_id
                )
            ],
            "leads": [
                item.model_dump(mode="json")
                for item in self._acquisition.list_leads(investigation.investigation_id)
            ],
        }

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationAuditInput.model_validate(value)
        self._read(request)
        records = self._store.list_audit(
            request.project_id,
            request.investigation_id,
            offset=request.offset,
            limit=request.limit,
        )
        return {
            "records": [record.model_dump(mode="json") for record in records],
            "offset": request.offset,
            "limit": request.limit,
            "returned": len(records),
        }

    async def start(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationStartInput.model_validate(value)
        thread = self._threads.read(request.thread_id)
        if thread is None or thread.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.THREAD_NOT_FOUND,
                "thread was not found in this project",
            )
        if request.parent_investigation_id is not None:
            parent = self._store.read(request.parent_investigation_id)
            if parent is None or parent.project_id != request.project_id:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "parent investigation was not found in this project",
                )
        try:
            investigation = self._service.start(
                thread=thread,
                trigger=request.trigger,
                question=request.question,
                parent_investigation_id=request.parent_investigation_id,
                target_object_id=request.target_object_id,
                target_hypothesis_id=request.target_hypothesis_id,
                mode=request.mode,
                scope=request.scope or thread.scope,
                required_evidence_groups=request.required_evidence_groups,
                query_families=request.query_families,
                budget=request.budget,
                stop_conditions=request.stop_conditions,
                explicit_saturation_opt_in=request.explicit_saturation_opt_in,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"investigation": investigation.model_dump(mode="json")}

    async def update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationUpdateInput.model_validate(value)
        current = self._read(request)
        try:
            updated = self._service.update_plan(
                current,
                expected_plan_revision=request.expected_plan_revision,
                mode=request.mode,
                scope=request.scope,
                required_evidence_groups=request.required_evidence_groups,
                query_families=request.query_families,
                budget_extension=request.budget_extension,
                stop_conditions=request.stop_conditions,
                explicit_saturation_opt_in=request.explicit_saturation_opt_in,
                reason=request.reason,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"investigation": updated.model_dump(mode="json")}

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationReadInput.model_validate(value)
        current = self._read(request)
        try:
            updated = self._service.pause(current)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"investigation": updated.model_dump(mode="json")}

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationResumeInput.model_validate(value)
        current = self._read(request)
        try:
            updated = self._service.resume(
                current,
                expected_checkpoint_digest=request.expected_checkpoint_digest,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.STALE_CHECKPOINT, str(exc)) from exc
        return {"investigation": updated.model_dump(mode="json")}

    async def stop(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = InvestigationStopInput.model_validate(value)
        current = self._read(request)
        try:
            updated = self._service.stop(current, reason=request.reason)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"investigation": updated.model_dump(mode="json")}

    def _read(self, request: InvestigationReadInput) -> InvestigationRecord:
        value = self._store.read(request.investigation_id)
        if value is None or value.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "investigation was not found in this project",
            )
        return value
