from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.field_measurement import FieldMeasurementService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ProtocolSealInput(ProjectInput):
    protocol_version: str = Field(min_length=1, max_length=160)
    case_digests: dict[str, str]
    arms: tuple[str, ...]
    sequence_matrix: tuple[str, ...]
    baseline_toolchain: tuple[str, ...]
    thresholds: dict[str, int]
    hard_zero_metrics: tuple[str, ...]


class SessionStartInput(ProjectInput):
    protocol_digest: str = Field(min_length=64, max_length=64)
    reviewer_external_ref: str = Field(min_length=1, max_length=500)
    case_id: str = Field(min_length=1, max_length=160)
    arm: str = Field(pattern=r"^[ABC]$")
    sequence_position: int = Field(ge=1)


class EventRecordInput(ProjectInput):
    session_id: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=80)
    metric_delta: int = Field(default=0, ge=0)
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class SessionEndInput(ProjectInput):
    session_id: str = Field(min_length=1, max_length=200)
    timeout: bool = False


class ScoreRecordInput(ProjectInput):
    session_id: str = Field(min_length=1, max_length=200)
    scorer_external_ref: str = Field(min_length=1, max_length=500)
    gold_issue_total: int = Field(ge=0)
    critical_issue_detected: int = Field(ge=0)
    decision_completeness_bps: int = Field(ge=0, le=10_000)
    source_span_valid_count: int = Field(ge=0)
    source_span_invalid_count: int = Field(ge=0)
    hard_zero_values: dict[str, int]


class ExportBuildInput(ProjectInput):
    protocol_digest: str = Field(min_length=64, max_length=64)
    purpose: str = Field(min_length=1, max_length=2_000)


class FieldMeasurementHandlers:
    def __init__(self, service: FieldMeasurementService) -> None:
        self._service = service

    async def protocol_seal(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProtocolSealInput.model_validate(value)
        try:
            result = self._service.seal_protocol(
                project_id=request.project_id,
                protocol_version=request.protocol_version,
                case_digests=request.case_digests,
                arms=request.arms,
                sequence_matrix=request.sequence_matrix,
                baseline_toolchain=request.baseline_toolchain,
                thresholds=request.thresholds,
                hard_zero_metrics=request.hard_zero_metrics,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"protocol": result.model_dump(mode="json")}

    async def session_start(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SessionStartInput.model_validate(value)
        try:
            result = self._service.start_session(**request.model_dump(mode="python"))
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"session": result.model_dump(mode="json")}

    async def event_record(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EventRecordInput.model_validate(value)
        try:
            result = self._service.record_event(**request.model_dump(mode="python"))
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"event": result.model_dump(mode="json")}

    async def session_end(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SessionEndInput.model_validate(value)
        try:
            session, metrics = self._service.end_session(**request.model_dump(mode="python"))
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "session": session.model_dump(mode="json"),
            "metrics": metrics.model_dump(mode="json"),
        }

    async def score_record(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ScoreRecordInput.model_validate(value)
        payload = request.model_dump(mode="python")
        project_id = str(payload.pop("project_id"))
        session_id = str(payload.pop("session_id"))
        scorer = str(payload.pop("scorer_external_ref"))
        authenticated = current_authenticated_actor()
        if authenticated is not None and (
            authenticated.project_id != project_id or authenticated.actor_id != scorer
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED,
                "authenticated scorer identity does not match the score subject",
                data={"reason_code": "AUTH_SCORER_IDENTITY_MISMATCH", "pre_io": True},
            )
        try:
            result = self._service.record_score(
                project_id=project_id,
                session_id=session_id,
                scorer_external_ref=scorer,
                values=cast(dict[str, object], payload),
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"score": result.model_dump(mode="json")}

    async def export_build(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportBuildInput.model_validate(value)
        try:
            result = self._service.build_export(**request.model_dump(mode="python"))
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"bundle": result.model_dump(mode="json")}
