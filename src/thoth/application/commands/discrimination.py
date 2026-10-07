"""hypothesis/test/result/record, hypothesis/refutation/record and hypothesis/test/result/list.

A person records what a discriminating test showed and what would show a hypothesis wrong. No
model is called and the hypothesis itself is not edited; the records are read beside it.
"""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.commands.verification_trace import require_trace_project
from thoth.application.services.discrimination_ledger import (
    DiscriminationLedger,
    DiscriminationRefused,
)
from thoth.application.services.human_actor import RECORD_HUMAN_ONLY
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.discrimination import MAX_CONDITIONS, MatchKind
from thoth.domain.enums import ProjectLifecycle
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ResultInput(ListInput):
    hypothesis_id: str = Field(min_length=1, max_length=200)
    test_id: str = Field(min_length=1, max_length=200)
    observation: str = Field(min_length=1, max_length=2_000)
    matched: MatchKind
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)


class ConditionsInput(ListInput):
    hypothesis_id: str = Field(min_length=1, max_length=200)
    conditions: tuple[str, ...] = Field(default=(), max_length=MAX_CONDITIONS)


class DiscriminationHandlers:
    def __init__(self, *, ledger: DiscriminationLedger, projects: ProjectStorePort) -> None:
        self._ledger, self._projects = ledger, projects

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        project = require_trace_project(self._projects, str(value.get("project_id", "")))
        closed = {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        if method != "hypothesis/test/result/list" and project.lifecycle in closed:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "closing or archived project cannot change a record"
            )

    @staticmethod
    def _actor() -> str:
        actor = current_authenticated_actor()
        return "human:local-user" if actor is None else actor.actor_id

    @staticmethod
    def _refused(exc: Exception) -> RpcApplicationError:
        code = (
            RpcErrorCode.AUTHORIZATION_DENIED
            if str(exc) == RECORD_HUMAN_ONLY
            else RpcErrorCode.DOMAIN_REJECTED
        )
        return RpcApplicationError(code, str(exc))

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ListInput.model_validate(value)
        self.authorize_before_claim("hypothesis/test/result/list", value)
        return {"items": self._ledger.view(request.project_id)}  # type: ignore[dict-item]

    async def record_result(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ResultInput.model_validate(value)
        self.authorize_before_claim("hypothesis/test/result/record", value)
        try:
            event = self._ledger.record_result(
                project_id=request.project_id,
                hypothesis_id=request.hypothesis_id,
                test_id=request.test_id,
                observation=request.observation,
                matched=request.matched,
                evidence_refs=request.evidence_refs,
                actor_id=self._actor(),
            )
        except (DiscriminationRefused, PermissionError) as exc:
            raise self._refused(exc) from exc
        return {
            "result": event.model_dump(mode="json"),
            "items": self._ledger.view(request.project_id, request.hypothesis_id),  # type: ignore[dict-item]
        }

    async def record_conditions(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ConditionsInput.model_validate(value)
        self.authorize_before_claim("hypothesis/refutation/record", value)
        try:
            event = self._ledger.record_conditions(
                project_id=request.project_id,
                hypothesis_id=request.hypothesis_id,
                conditions=request.conditions,
                actor_id=self._actor(),
            )
        except (DiscriminationRefused, PermissionError) as exc:
            raise self._refused(exc) from exc
        return {
            "conditions": event.model_dump(mode="json"),
            "items": self._ledger.view(request.project_id, request.hypothesis_id),  # type: ignore[dict-item]
        }
