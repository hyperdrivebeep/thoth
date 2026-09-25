"""HOSTED_REVIEW model catalog, resolver, ready projection, and RPC guards."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from openai import AsyncOpenAI
from pydantic import JsonValue

from thoth.adapters.http.hosted_review import hosted_dispatch_gate_enabled
from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.models.companies import COMPANIES
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.adapters.storage.hosted_review_budget import (
    HostedReviewProviderBudgetExceeded,
    SqliteHostedReviewProviderBudget,
)
from thoth.application.services.request_records import RequestRecords
from thoth.apps.hosted_review_quota import quota_rejected
from thoth.domain.deployment_mode import STALE_RUNNING_MESSAGE, STALE_RUNNING_REASON
from thoth.domain.enums import OperationState, ProjectLifecycle
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.domain.operation import OperationRecord
from thoth.domain.research_request import ResearchAttempt
from thoth.domain.resource_scope import resource_use_scope
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.model import ModelExecutionHold, ModelPort, ModelResolutionError
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import CommandHandler, MethodRegistry

HOSTED_REVIEW_RESEARCH_METHODS = frozenset({"thread/start", "thread/input", "thread/steer"})

HOSTED_REVIEW_DEFAULT_MODEL = "gpt-5.5"
HOSTED_REVIEW_MODEL_ALLOWLIST = frozenset({HOSTED_REVIEW_DEFAULT_MODEL})
HOSTED_REVIEW_OPENAI_BASE_URL = COMPANIES["openai"]["base_url"]
HOSTED_REVIEW_DISCLOSURE = (
    "질문과 분석에 쓰인 자료는 운영자 OpenAI API로 전달됩니다."
)


@dataclass(frozen=True)
class HostedReviewModelContract:
    provider: str
    model: str
    base_url: str


def hosted_operator_key_present() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def hosted_review_contract() -> HostedReviewModelContract:
    provider = os.environ.get("HOSTED_REVIEW_PROVIDER", "openai").strip().lower() or "openai"
    model = (
        os.environ.get("HOSTED_REVIEW_MODEL", HOSTED_REVIEW_DEFAULT_MODEL).strip()
        or HOSTED_REVIEW_DEFAULT_MODEL
    )
    base_url = (
        os.environ.get("OPENAI_BASE_URL", HOSTED_REVIEW_OPENAI_BASE_URL).strip()
        or HOSTED_REVIEW_OPENAI_BASE_URL
    )
    if provider != "openai":
        raise RuntimeError("HOSTED_REVIEW_PROVIDER_UNSUPPORTED")
    if model not in HOSTED_REVIEW_MODEL_ALLOWLIST:
        raise RuntimeError("HOSTED_REVIEW_MODEL_UNSUPPORTED")
    if base_url.rstrip("/") != HOSTED_REVIEW_OPENAI_BASE_URL.rstrip("/"):
        raise RuntimeError("HOSTED_REVIEW_BASE_URL_LOCKED")
    return HostedReviewModelContract(
        provider="openai",
        model=model,
        base_url=HOSTED_REVIEW_OPENAI_BASE_URL,
    )


def hosted_review_catalog() -> StaticModelCatalog:
    contract = hosted_review_contract()
    option = ModelOption(
        provider=contract.provider,
        model=contract.model,
        reasoning_efforts=("none", "low", "medium", "high", "xhigh"),
        default_effort="medium",
        capability_source="hosted-review/openai-responses-v1",
    )
    return StaticModelCatalog(
        (option,),
        ModelSelection(
            provider=contract.provider,
            model=contract.model,
            reasoning_effort="medium",
        ),
    )


def hosted_openai_resolver() -> RegisteredModelResolver:
    contract = hosted_review_contract()
    resolver = RegisteredModelResolver()
    budget = SqliteHostedReviewProviderBudget(
        Path(os.environ.get("THOTH_WORKSPACE", ".thoth")).resolve()
        / "hosted-review-control"
        / "provider-budget.sqlite3",
        max_requests_per_day=int(
            os.environ.get("HOSTED_REVIEW_MAX_OPENAI_REQUESTS_PER_DAY", "200")
        ),
    )

    def reserve_provider_request() -> None:
        try:
            budget.reserve()
        except HostedReviewProviderBudgetExceeded as exc:
            raise quota_rejected("HOSTED_REVIEW_OPENAI_LIMIT") from exc

    def factory(model: str | None) -> ModelPort:
        chosen = contract.model if model in {None, "", contract.model} else model
        if chosen != contract.model:
            raise ModelResolutionError("MODEL_ROUTE_LOCKED")
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ModelExecutionHold("HOSTED_REVIEW_MODEL_UNAVAILABLE")
        if hosted_dispatch_gate_enabled():
            raise ModelExecutionHold("HOSTED_REVIEW_PROVIDER_BUDGET_UNAVAILABLE")
        client = AsyncOpenAI(api_key=key, base_url=contract.base_url, max_retries=0)
        return OpenAIResponsesModel(
            client, model_id=contract.model, before_request=reserve_provider_request
        )

    resolver.register(contract.provider, factory)
    resolver.register("default", factory)
    return resolver


def hosted_ready_projection(
    setup: WorkspaceSetupState,
    *,
    key_present: bool | None = None,
) -> dict[str, JsonValue]:
    contract = hosted_review_contract()
    present = hosted_operator_key_present() if key_present is None else key_present
    present = present and not hosted_dispatch_gate_enabled()
    consent_ready = setup.internet_consent in {"ALLOWED", "DENIED"}
    return {
        "ready": consent_ready and not hosted_dispatch_gate_enabled(),
        "setup": cast(JsonValue, setup.model_dump(mode="json")),
        "model_connected": present,
        "deployment_mode": "HOSTED_REVIEW",
        "credential_mode": "SERVER_MANAGED",
        "hosted_model": {"provider": contract.provider, "model": contract.model},
        "disclosure": HOSTED_REVIEW_DISCLOSURE,
    }


async def deny_hosted_credentials(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    del value
    raise RpcApplicationError(
        RpcErrorCode.AUTHORIZATION_DENIED,
        "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED",
        data={"reason_code": "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED", "pre_io": True},
    )


def lock_hosted_model_route(
    original: CommandHandler,
) -> CommandHandler:
    contract = hosted_review_contract()

    async def hosted_update(value: dict[str, JsonValue]):
        selection = value.get("selection")
        if isinstance(selection, dict):
            provider = selection.get("provider")
            model = selection.get("model")
            if provider not in {None, "default", contract.provider}:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "MODEL_ROUTE_LOCKED",
                    data={"reason_code": "MODEL_ROUTE_LOCKED", "pre_io": True},
                )
            if model not in {None, contract.model}:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "MODEL_ROUTE_LOCKED",
                    data={"reason_code": "MODEL_ROUTE_LOCKED", "pre_io": True},
                )
        return await original(value)

    return hosted_update


def hosted_active_project_count(projects: ProjectStorePort) -> int:
    return sum(
        1
        for project in projects.list()
        if project.lifecycle != ProjectLifecycle.ARCHIVED_READ_ONLY
    )


def install_hosted_review_rpc_guards(
    registry: MethodRegistry,
    *,
    projects: ProjectStorePort | None = None,
    operations: OperationStorePort | None = None,
    records: RequestRecords | None = None,
    max_projects: int = 3,
    max_concurrent_research: int = 1,
) -> None:
    registry.decorate("model/credential/list", lambda _: deny_hosted_credentials)
    registry.decorate("model/credential/register", lambda _: deny_hosted_credentials)
    registry.decorate("model/settings/update", lock_hosted_model_route)
    if projects is not None:

        def wrap_create(original: CommandHandler) -> CommandHandler:
            async def limited(value: dict[str, JsonValue]):
                if hosted_active_project_count(projects) >= max_projects:
                    raise quota_rejected("HOSTED_REVIEW_PROJECT_LIMIT")
                return await original(value)

            return limited

        registry.decorate("project/create", wrap_create)
    if operations is None or projects is None:
        return

    def same_thread_operation(operation: OperationRecord, target: str) -> bool:
        if records is None:
            return False
        attempt = records.journal_read(
            operation.project_id, operation.operation_id, ResearchAttempt
        )
        if attempt is not None:
            return attempt.continuation.get("thread_id") == target
        queue = records.controls.read(
            operation.project_id,
            "RESEARCH_EXECUTION",
            f"queue:{operation.operation_id}",
        )
        return bool(
            queue is not None
            and queue.record_type == "QueuedResearchInput"
            and queue.payload.get("thread_id") == target
        )

    def wrap_start(
        original: CommandHandler, *, same_thread_allowed: bool = False
    ) -> CommandHandler:
        async def limited(value: dict[str, JsonValue]):
            if same_thread_allowed and value.get("contract_version") != 2:
                return await original(value)
            active = current_operation.get()
            active_id = active.operation_id if active is not None else None
            running: list[OperationRecord] = []
            for project in projects.list():
                for operation in operations.list_by_project(project.project_id):
                    if operation.method not in HOSTED_REVIEW_RESEARCH_METHODS:
                        continue
                    if operation.state not in {OperationState.PENDING, OperationState.RUNNING}:
                        continue
                    if active_id is not None and operation.operation_id == active_id:
                        continue
                    queue = (
                        None
                        if records is None
                        else records.controls.read(
                            operation.project_id,
                            "RESEARCH_EXECUTION",
                            f"queue:{operation.operation_id}",
                        )
                    )
                    if (
                        queue is not None
                        and queue.record_type == "QueuedResearchInput"
                        and queue.state in {"HOLD", "SUPERSEDED"}
                    ):
                        continue
                    running.append(operation)
            if len(running) >= max_concurrent_research:
                target = value.get("thread_id")
                same_thread = (
                    same_thread_allowed
                    and isinstance(target, str)
                    and bool(running)
                    and all(
                        operation.project_id == value.get("project_id")
                        and same_thread_operation(operation, target)
                        for operation in running
                    )
                )
                if not same_thread:
                    raise quota_rejected("HOSTED_REVIEW_RESEARCH_LIMIT")
            return await original(value)

        return limited

    registry.decorate("thread/start", wrap_start)
    registry.decorate(
        "thread/input", lambda original: wrap_start(original, same_thread_allowed=True)
    )
    registry.decorate(
        "thread/steer", lambda original: wrap_start(original, same_thread_allowed=True)
    )


def fail_stale_running_operations(
    operations: OperationStorePort,
    projects: ProjectStorePort,
    *,
    controls: ControlRecordStorePort | None = None,
    completed_at: datetime | None = None,
) -> int:
    closed = 0
    moment = completed_at or datetime.now(UTC)
    error: dict[str, JsonValue] = {
        "code": int(RpcErrorCode.DOMAIN_REJECTED),
        "message": STALE_RUNNING_MESSAGE,
        "data": {
            "reason_code": STALE_RUNNING_REASON,
            "remote_observation": "UNKNOWN",
        },
    }
    for project in projects.list():
        for operation in operations.list_by_project(project.project_id):
            if operation.state not in {OperationState.PENDING, OperationState.RUNNING}:
                continue
            queue = (
                None
                if controls is None
                else controls.read(
                    project.project_id,
                    "RESEARCH_EXECUTION",
                    f"queue:{operation.operation_id}",
                )
            )
            if (
                queue is not None
                and queue.record_type == "QueuedResearchInput"
                and queue.state in {"QUEUED", "HOLD"}
            ):
                continue
            with resource_use_scope(operation.project_id):
                operations.fail(operation.operation_id, error, completed_at=moment)
            closed += 1
    return closed
