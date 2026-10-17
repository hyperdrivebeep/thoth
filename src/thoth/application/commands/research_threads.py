"""v2 durable admission and nonblocking research on the existing Thread API."""

from collections.abc import Awaitable, Callable
from typing import Literal, cast

from pydantic import JsonValue

from thoth.application.commands.research_resume import resume_payload
from thoth.application.commands.thread_analysis import ThreadInputRequest
from thoth.application.commands.threads import ThreadCommandHandlers
from thoth.application.commands.trace_origin_admission import admit_trace_origin
from thoth.application.services.connector_cleanup import cleanup_summary
from thoth.application.services.expected_result_view import base_read_params, expected_result_view
from thoth.application.services.model_call_settings import ModelCallSettingsService
from thoth.application.services.model_settings import ModelSettingsService
from thoth.application.services.post_execution_memory import PostExecutionMemory
from thoth.application.services.provider_usage_service import ProviderUsageService
from thoth.application.services.request_language import (
    legacy_start_params,
    research_start_params,
    validate_request_language,
    with_request_language,
)
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_analysis import ResearchAnalysis
from thoth.application.services.research_attempt_admission import (
    accepted_attempt_value,
    pending_attempt,
)
from thoth.application.services.research_attempt_runner import run_research_attempt
from thoth.application.services.research_controls import ResearchControls
from thoth.application.services.research_conversation_activity import read_conversation_activity
from thoth.application.services.research_input_queue import ResearchInputQueue
from thoth.application.services.research_leases import ResearchLeases
from thoth.application.services.research_operation_access import ResearchOperationAccess
from thoth.application.services.research_read_summary import VIEWS
from thoth.application.services.research_result_publication import publish_research_result
from thoth.domain.account_usage import AccountQuotaSnapshot
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.enums import EntityType, OperationState, ThreadLifecycle
from thoth.domain.model_settings import ModelSelection
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import (
    AuthoredInputRevision,
    InputDelivery,
    ResearchAttempt,
    ResearchBudget,
    RevisionRef,
    ThreadRequestRevision,
    decode_thread_request,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.test_validity import TestValidityAssessment
from thoth.ports.command_authorization import CommandAuthorizerPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.model import ModelResolutionError, ModelResolverPort
from thoth.ports.operation import OperationReentryDenied, OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.provider_usage import ProviderUsagePort
from thoth.ports.research_queue import ResearchQueueStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.deferred import (
    AcceptedRunning,
    EphemeralCommandResult,
    PendingExecution,
    current_operation,
)
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import CommandHandler

LegacyAnalysis = Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class ResearchThreadHandlers:
    def __init__(
        self,
        *,
        legacy: ThreadCommandHandlers,
        analyze: CommandHandler,
        records: RequestRecords,
        analysis: ResearchAnalysis,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
        governance: GovernanceStorePort,
        operations: OperationStorePort,
        models: ModelResolverPort,
        access: ResourceAccessPort,
        leases: ResearchLeases,
        model_settings: ModelSettingsService,
        queue_store: ResearchQueueStorePort,
        assessment_verifier: Callable[[str, str], TestValidityAssessment] | None = None,
        provider_usage: ProviderUsagePort | None = None,
        call_settings: ModelCallSettingsService | None = None,
    ) -> None:
        self.legacy, self.analyze_legacy, self.records, self.analysis = (
            legacy,
            analyze,
            records,
            analysis,
        )
        self.projects, self.threads, self.governance = projects, threads, governance
        self.operations, self.models, self.access = operations, models, access
        self.leases = leases
        self.operation_access = ResearchOperationAccess(access)
        self.controls = ResearchControls(self)
        self.model_settings = model_settings
        self.call_settings = call_settings
        self.queue = ResearchInputQueue(queue_store)
        self.provider_usage = ProviderUsageService(records, provider_usage)
        self.assessment_verifier = assessment_verifier
        # Called after a terminal result is durable; failures never fail the research.
        self.on_result_published: Callable[..., object] | None = None

    def authorize_reentry(self, operation: OperationRecord) -> None:
        if operation.state != OperationState.RUNNING:
            return
        try:
            self.operation_access.require_execution_owner(operation)
        except (RpcApplicationError, ResourceScopeError, PermissionError) as exc:
            raise OperationReentryDenied("AUTH_OPERATION_OWNER_DENIED") from exc

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return await self.controls.pause(value)

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        return await self.controls.resume(value)

    async def stop(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return await self.controls.stop(value)

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        self.legacy.authorize_before_claim(method, value)
        if method == "thread/input" or (
            method == "thread/steer" and value.get("contract_version") == 2
        ):
            owner = getattr(self.analyze_legacy, "__self__", None)
            if isinstance(owner, CommandAuthorizerPort):
                data = {
                    key: item
                    for key, item in value.items()
                    if key in ThreadInputRequest.model_fields
                }
                owner.authorize_before_claim(
                    "thread/input", data if value.get("contract_version") == 2 else value
                )

    def admission(self, operation: OperationRecord) -> dict[str, JsonValue] | None:
        return self.queued_admission(operation)

    def queued_admission(self, operation: OperationRecord) -> dict[str, JsonValue] | None:
        attempt = self.records.journal_read(
            operation.project_id, operation.operation_id, ResearchAttempt
        )
        if attempt is not None:
            return self.accepted_value(attempt)
        return self.queue.admission(operation)

    def accepted_value(self, attempt: ResearchAttempt) -> dict[str, JsonValue]:
        return accepted_attempt_value(attempt)

    def pending(self, attempt: ResearchAttempt, status: str) -> PendingExecution:
        return pending_attempt(self, attempt, status)

    async def start(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        validate_request_language(value)
        if value.get("contract_version") != 2:
            return await self.legacy.start(value)
        with self.records.ledger.transaction():
            operation = current_operation.get()
            if operation is None:
                raise ValueError("RPC_OPERATION_REQUIRED")
            self.authorize_reentry(operation)
            existing = self.records.journal_read(
                operation.project_id, operation.operation_id, ResearchAttempt
            )
            if existing is not None:
                return self.replay_attempt(existing, operation)
            value = admit_trace_origin(self, "thread/start", value)
            result = await self.legacy.start(legacy_start_params(value))
            return self.submit(research_start_params(value, result["thread_id"]), initial=True)

    async def input(
        self, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | AcceptedRunning | PendingExecution | EphemeralCommandResult:
        validate_request_language(value)
        if value.get("contract_version") != 2:
            return await self.analyze_legacy(value)
        payload = {k: v for k, v in value.items() if k != "contract_version"}
        operation = current_operation.get()
        if operation is None:
            raise ValueError("RPC_OPERATION_REQUIRED")
        self.authorize_reentry(operation)
        existing = self.records.journal_read(
            operation.project_id, operation.operation_id, ResearchAttempt
        )
        if existing is not None:
            return self.replay_attempt(existing, operation)
        payload = admit_trace_origin(self, "thread/input", payload)
        if payload.get("resume_from_operation_id") is not None:
            payload = resume_payload(self, payload)
        queued = self.queue.accept_if_busy(self, operation, payload)
        if queued is not None:
            return queued
        return self.submit(payload)

    async def steer(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        validate_request_language(value)
        if value.get("contract_version") != 2:
            return await self.legacy.steer(value)
        admit_trace_origin(self, "thread/steer", value)
        with self.records.ledger.transaction():
            submitted = self.submit(
                {
                    **{k: v for k, v in value.items() if k != "contract_version"},
                    "edit_kind": "STEER",
                }
            )
            self.queue.hold_for_steer(self, str(value["project_id"]), str(value["thread_id"]))
            return submitted

    def submit(
        self,
        value: dict[str, JsonValue],
        initial: bool = False,
        queued_input_id: str | None = None,
    ) -> AcceptedRunning:
        operation = current_operation.get()
        if operation is None:
            raise ValueError("RPC_OPERATION_REQUIRED")
        self.authorize_reentry(operation)
        project_id, thread_id = str(value["project_id"]), str(value["thread_id"])
        self.authorize_before_claim("thread/input", {**value, "contract_version": 2})
        with self.records.ledger.transaction():
            existing = self.records.journal_read(
                project_id, operation.operation_id, ResearchAttempt
            )
            if existing is not None:
                return self.replay_attempt(existing, operation)
            thread, project = self.threads.read(thread_id), self.projects.read(project_id)
            if thread is None or thread.project_id != project_id or project is None:
                raise RpcApplicationError(
                    RpcErrorCode.THREAD_NOT_FOUND, "thread not found in project"
                )
            if thread.lifecycle != ThreadLifecycle.OPEN:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "terminal thread cannot accept input"
                )
            prior = self.records.read(project_id, EntityType.THREAD, f"request:{thread_id}")
            try:
                settings = self.model_settings.resolve(
                    project_id,
                    thread_id,
                    ModelSelection.model_validate(
                        {
                            k: value[k]
                            for k in ("provider", "model", "reasoning_effort")
                            if k in value
                        }
                    ),
                )
            except ModelResolutionError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            if value.get("retry_policy") == "ONCE_TRANSIENT_429":
                from thoth.domain.oauth_retry import allows_once_transient_429

                if not allows_once_transient_429(
                    provider=settings.provider,
                    capability_source=settings.capability_source,
                ):
                    raise RpcApplicationError(
                        RpcErrorCode.DOMAIN_REJECTED, "RETRY_POLICY_UNSUPPORTED"
                    )
            value = {**value, "provider": settings.provider, "model": settings.model}
            previous = None if prior is None else decode_thread_request(prior[1])
            epoch = 0 if previous is None else previous.request_epoch
            edit_kind = str(value.get("edit_kind", "APPEND"))
            if edit_kind not in {"APPEND", "REPLACE", "STEER"}:
                raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "unsupported edit_kind")
            expected_epoch = value.get("expected_request_epoch")
            if edit_kind != "APPEND" and (
                not isinstance(expected_epoch, int)
                or isinstance(expected_epoch, bool)
                or expected_epoch != epoch
            ):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "REQUEST_EPOCH_CONFLICT")
            instruction = value.get("instruction")
            if (
                not isinstance(instruction, str)
                or not instruction.strip()
                or len(instruction) > 20_000
            ):
                raise RpcApplicationError(
                    RpcErrorCode.INVALID_PARAMS, "non-empty instruction required"
                )
            prior_question = thread.problem if previous is None else previous.effective_question
            question = (
                instruction
                if initial or edit_kind == "REPLACE"
                else prior_question + "\n\n" + instruction
            )
            input_id = queued_input_id or self.records.ids.new("input")
            actor = current_authenticated_actor()
            author = "human:local-user" if actor is None else actor.actor_id
            policy = self.governance.read_policy(project_id)
            if policy is None:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "POLICY_MISSING")
            text_ref = self.records.save(
                project_id,
                EntityType.THREAD,
                f"input:{input_id}",
                AuthoredInputRevision(
                    project_id=project_id,
                    thread_id=thread_id,
                    input_id=input_id,
                    text=instruction,
                    actor_ref=author,
                ),
            )
            request = ThreadRequestRevision(
                project_id=project_id,
                thread_id=thread_id,
                request_epoch=epoch + 1,
                parent_ref=None if prior is None else prior[0],
                accepted_input_ids=(
                    ()
                    if previous is None or edit_kind == "REPLACE"
                    else previous.accepted_input_ids
                )
                + (input_id,),
                edit_kind=cast(Literal["APPEND", "REPLACE", "STEER"], edit_kind),
                authored_text=instruction,
                authored_text_ref=text_ref,
                effective_question=question,
                scope=thread.scope,
                cutoff_at=project.cutoff_at.isoformat(),
                policy_ref=project.policy_binding_ref,
                policy_digest=policy.policy_digest,
                actor_ref="human:local-user" if actor is None else actor.actor_id,
                operation_id=operation.operation_id,
                model_settings=settings,
            )
            request = with_request_language(request, value)
            ref = self.records.save(project_id, EntityType.THREAD, f"request:{thread_id}", request)
            if previous is not None and edit_kind == "REPLACE":
                for prior_input_id in previous.accepted_input_ids:
                    prior_delivery = self.records.journal_read(
                        project_id, f"input:{prior_input_id}", InputDelivery
                    )
                    if prior_delivery is not None:
                        self.records.journal(
                            project_id,
                            f"input:{prior_input_id}",
                            prior_delivery.model_copy(update={"state": "SUPERSEDED"}),
                            "SUPERSEDED",
                        )
            attempt = ResearchAttempt(
                operation_id=operation.operation_id,
                request_ref=ref,
                owner_actor_id=operation.owner_actor_id,
                owner_session_id=operation.owner_session_id,
                captured_heads=dict(self.records.ledger.read_heads(project_id)),
                budget_ref=f"budget:{thread_id}",
                continuation={
                    **value,
                    "request_epoch": epoch + 1,
                    "input_id": input_id,
                    "retry_policy": value.get("retry_policy"),
                    "prior_operation_id": value.get("prior_operation_id"),
                    "resume_from_operation_id": value.get("resume_from_operation_id"),
                },
            )
            self.records.journal(project_id, operation.operation_id, attempt)
            event_cursor = None
            event = None
            if self.records.events is not None:
                event = self.records.events.append(
                    project_id=project_id,
                    operation_id=operation.operation_id,
                    event_type="research.accepted",
                    payload={
                        "request_ref": ref.model_dump(mode="json"),
                        "input_id": input_id,
                        "request_epoch": epoch + 1,
                    },
                )
            self.records.journal(
                project_id,
                f"input:{input_id}",
                InputDelivery(input_id=input_id, ordinal=epoch + 1, request_ref=ref),
                "ACCEPTED",
            )
            if event is not None:
                event_cursor = event.event_id
            if event_cursor is not None:
                attempt = attempt.model_copy(update={"event_cursor": event_cursor})
                self.records.journal(project_id, operation.operation_id, attempt)
            if self.records.journal_read(project_id, f"budget:{thread_id}", ResearchBudget) is None:
                self.records.journal(
                    project_id,
                    f"budget:{thread_id}",
                    ResearchBudget(started_at=self.records.clock.now().isoformat()),
                )
        return AcceptedRunning(
            self.accepted_value(attempt), lambda: self.run(attempt, request, operation)
        )

    def replay_attempt(
        self, attempt: ResearchAttempt, operation: OperationRecord
    ) -> AcceptedRunning:
        self.authorize_reentry(operation)
        revision = self.records.ledger.read_revision_by_digest(
            operation.project_id, attempt.request_ref.revision_digest
        )
        snapshot = (
            None if revision is None else self.records.ledger.read_snapshot(revision.snapshot_id)
        )
        if snapshot is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "RESUBMIT_REQUIRED_REQUEST_MISSING"
            )
        request = decode_thread_request(snapshot.content)
        return AcceptedRunning(
            self.accepted_value(attempt), lambda: self.run(attempt, request, operation)
        )

    async def run(
        self, attempt: ResearchAttempt, request: ThreadRequestRevision, operation: OperationRecord
    ) -> dict[str, JsonValue] | PendingExecution:
        return await run_research_attempt(self, attempt, request, operation)

    def publish(
        self,
        attempt: ResearchAttempt,
        request: ThreadRequestRevision,
        work: ResearchWork,
        phase: str,
        result: dict[str, object],
        gaps: tuple[str, ...],
        terminal: str | None = None,
        check: bool = True,
    ) -> RevisionRef:
        return publish_research_result(
            attempt,
            request,
            work,
            phase,
            result,
            gaps,
            terminal,
            check,
            records=self.records,
            operations=self.operations,
            access=self.access,
            source_digest=lambda spans: self.analysis.source_digest(spans),
        )

    async def activity_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return await read_conversation_activity(
            value,
            records=self.records,
            access=self.access,
            legacy_activity_list=lambda params: self.legacy.activity_list(params),
        )

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if value.get("view", "SUMMARY") not in VIEWS:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "THREAD_READ_VIEW_UNKNOWN")
        base = await self.legacy.read(base_read_params(value))
        # A requested account-quota refresh is a provider call: it runs here, outside the read
        # transaction and its approval memo, and the read only receives its result.
        quota = (
            self.provider_usage.refresh(str(value["project_id"]), str(value["thread_id"]))
            if value.get("refresh_account_quota") is True
            else None
        )
        return expected_result_view(
            self._read_current(value, base, quota), value.get("expected_result_contract")
        )

    def _read_current(
        self,
        value: dict[str, JsonValue],
        base: dict[str, JsonValue],
        quota: AccountQuotaSnapshot | None,
    ) -> dict[str, JsonValue]:
        # One read transaction and one approval memo for the whole read, so a record it touches
        # again is not re-approved from its first ancestor each time.
        from thoth.application.services.resource_scope_read_context import (
            scope_read_transaction,
        )

        with self.records.ledger.transaction(), scope_read_transaction():
            return self._read_current_scoped(value, base, quota)

    def _read_current_scoped(
        self,
        value: dict[str, JsonValue],
        base: dict[str, JsonValue],
        quota: AccountQuotaSnapshot | None,
    ) -> dict[str, JsonValue]:
        project_id, thread_id = str(value["project_id"]), str(value["thread_id"])
        from thoth.application.services.research_read_payload import (
            model_dispatch_fields,
            snapshot_fields,
        )
        from thoth.application.services.research_thread_read_snapshot import (
            read_research_snapshot,
        )
        from thoth.application.services.research_usage_read import read_thread_usage

        snapshot = read_research_snapshot(self, project_id, thread_id)
        if snapshot is None:
            return base
        current_ref, request, attempt = snapshot.current_ref, snapshot.request, snapshot.attempt
        manifest = snapshot.manifest
        fresh, budget = snapshot.fresh, snapshot.budget
        dispatches, op = snapshot.dispatches, snapshot.operation
        usage, result_usage = read_thread_usage(
            project_id=project_id,
            thread_id=thread_id,
            snapshot=snapshot,
            quota=quota,
            provider_usage=self.provider_usage,
            operations=self.operations,
        )
        from thoth.domain.research_failure import ResearchCleanupFailure, ResearchFailureRecord

        failure = (
            None
            if attempt is None
            else self.records.journal_read(
                project_id,
                f"failure:{request.operation_id}:{attempt.attempt_epoch}",
                ResearchFailureRecord,
            )
        )
        cleanup_failure = (
            None
            if attempt is None
            else self.records.journal_read(
                project_id,
                f"failure-cleanup:{request.operation_id}:{attempt.attempt_epoch}",
                ResearchCleanupFailure,
            )
        )
        effective = "UNKNOWN" if op is None else op.state.value
        from thoth.application.services.research_completion_view import completion_view

        completion = completion_view(
            self.records,
            self.access,
            attempt,
            manifest,
            fresh,
            effective,
            str(base.get("execution_state", "UNKNOWN")),
            budget,
        )
        payload = {
            **snapshot_fields(snapshot, base, completion, failure, cleanup_failure),
            "inputs": [
                d.model_dump(mode="json")
                for i in request.accepted_input_ids
                if (d := self.records.journal_read(project_id, f"input:{i}", InputDelivery))
                is not None
            ],
            "budget": None if budget is None else budget.model_dump(mode="json"),
            "usage": usage,
            "result_usage": result_usage,
            "model_dispatches": model_dispatch_fields(dispatches),
            "cleanup": cleanup_summary(self.records.controls, project_id, request.operation_id),
            "post_execution_learning_current": [
                learning.model_dump(mode="json")
                for learning in PostExecutionMemory(self.records, self.analysis.memory).current(
                    project_id, thread_id
                )
                if learning.basis.request_ref == current_ref
            ],
            "queued_inputs_v2": list(self.queue.summary(project_id, thread_id)),
        }
        from thoth.application.services.research_read_view import project_thread_read_view

        return project_thread_read_view(
            value=value,
            payload=payload,
            snapshot=snapshot,
            effective=effective,
            ledger=self.records.ledger,
            access=self.access,
        )
