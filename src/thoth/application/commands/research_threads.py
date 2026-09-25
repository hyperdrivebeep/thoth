"""v2 durable admission and nonblocking research on the existing Thread API."""

from collections.abc import Awaitable, Callable
from typing import Literal, cast

from pydantic import JsonValue

from thoth.application.commands.thread_analysis import ThreadInputRequest
from thoth.application.commands.threads import ThreadCommandHandlers
from thoth.application.services.connector_cleanup import cleanup_summary
from thoth.application.services.model_settings import ModelSettingsService
from thoth.application.services.post_execution_memory import PostExecutionMemory
from thoth.application.services.provider_usage_service import ProviderUsageService
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_analysis import ResearchAnalysis
from thoth.application.services.research_attempt_admission import (
    accepted_attempt_value,
    pending_attempt,
)
from thoth.application.services.research_attempt_runner import run_research_attempt
from thoth.application.services.research_controls import ResearchControls
from thoth.application.services.research_input_queue import ResearchInputQueue
from thoth.application.services.research_leases import ResearchLeases
from thoth.application.services.research_operation_access import ResearchOperationAccess
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType, OperationState, ThreadLifecycle
from thoth.domain.model_settings import ModelSelection
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchFence, ResearchWork
from thoth.domain.research_request import (
    AuthoredInputRevision,
    InputDelivery,
    ResearchAttempt,
    ResearchBudget,
    RevisionRef,
    ThreadRequestRevision,
)
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
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
from thoth.protocol.deferred import AcceptedRunning, PendingExecution, current_operation
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
        self.queue = ResearchInputQueue(queue_store)
        self.provider_usage = ProviderUsageService(records, provider_usage)
        self.assessment_verifier = assessment_verifier

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
            data = {
                k: v
                for k, v in value.items()
                if k not in {"contract_version", "provider", "model", "reasoning_effort"}
            }
            result = await self.legacy.start(data)
            return self.submit(
                {
                    "project_id": value["project_id"],
                    "thread_id": result["thread_id"],
                    "instruction": value["problem"],
                    "provider": value.get("provider", "default"),
                    "model": value.get("model"),
                    "reasoning_effort": value.get("reasoning_effort"),
                },
                initial=True,
            )

    async def input(
        self, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | AcceptedRunning | PendingExecution:
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
        queued = self.queue.accept_if_busy(self, operation, payload)
        if queued is not None:
            return queued
        return self.submit(payload)

    async def steer(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        if value.get("contract_version") != 2:
            return await self.legacy.steer(value)
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
            previous = None if prior is None else ThreadRequestRevision.model_validate(prior[1])
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
        request = ThreadRequestRevision.model_validate(snapshot.content)
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
        with self.records.ledger.transaction():
            if check:
                work.boundary.check()
            if not work.boundary.owns_attempt():
                from thoth.domain.research_lease import ResearchLeaseLost

                raise ResearchLeaseLost("ATTEMPT_LEASE_FENCED")
            # Recheck request even on budget/transport HOLD; stale work cannot become current.
            head = self.records.read(
                request.project_id, EntityType.THREAD, f"request:{request.thread_id}"
            )
            if head is None or head[0] != attempt.request_ref:
                raise ResearchFence("STALE_PUBLICATION")
            uses = current_resource_uses() or ()
            operation = self.operations.read(attempt.operation_id)
            if operation is None:
                raise ResearchFence("OPERATION_MISSING")
            self.access.require_operation(operation.model_copy(update={"resource_uses": uses}))
            staged = tuple(work.pending_revisions)
            self.records.commit_staged(staged)
            from thoth.application.services.research_basis_capture import capture_record_producers

            capture_record_producers(work, self.records.ledger)
            from thoth.application.services.research_basis_capture import result_basis
            from thoth.domain.research_request import CurrentResultManifestV21

            manifest = CurrentResultManifestV21(
                research_basis=result_basis(work, request, self.records.ledger),
                request_ref=attempt.request_ref,
                operation_id=attempt.operation_id,
                attempt_epoch=attempt.attempt_epoch,
                basis_digest=domain_digest(
                    "RESEARCH_BASIS",
                    "2.0.0",
                    canonical_payload(
                        {
                            "request": attempt.request_ref,
                            "refs": work.record_refs,
                            "evidence": work.evidence,
                            "uses": uses,
                        }
                    ),
                ),
                phase=phase,
                completion="TERMINAL" if terminal else "CHECKPOINT",
                input_ids=request.accepted_input_ids,
                record_refs=tuple(work.record_refs),
                source_refs=tuple(s.span_id for s in work.evidence),
                resource_uses=tuple(u.model_dump(mode="python") for u in uses),
                source_context_digest=self.analysis.source_digest(work.evidence),
                source_context_version="2.1.0",
                source_basis={
                    s.span_id: f"{s.source_version_id}:{s.text_sha256}" for s in work.evidence
                },
                result=result,
                gaps=gaps,
                next_steps=gaps,
                terminal_reason=terminal,
            )
            ref = self.records.save(
                request.project_id,
                EntityType.DECISION_OBJECT,
                f"result:{request.thread_id}",
                manifest,
                evidence_refs=manifest.source_refs,
            )
            self.records.journal(
                request.project_id,
                attempt.operation_id,
                (
                    self.records.journal_read(
                        request.project_id, attempt.operation_id, ResearchAttempt
                    )
                    or attempt
                ).model_copy(
                    update={
                        "phase": phase,
                        "checkpoint_ref": ref,
                        "status": "SUCCEEDED" if terminal else "RUNNING",
                    }
                ),
            )
            # Input is APPLIED only after an analysis result, not its acceptance checkpoint.
            if phase != "CONNECTED_SOURCES" and "requirements" in work.context:
                for input_id in request.accepted_input_ids:
                    delivery = self.records.journal_read(
                        request.project_id, f"input:{input_id}", InputDelivery
                    )
                    if delivery is not None and delivery.first_result_ref is None:
                        self.records.journal(
                            request.project_id,
                            f"input:{input_id}",
                            delivery.model_copy(
                                update={"state": "APPLIED", "first_result_ref": ref}
                            ),
                            "APPLIED",
                        )
            event_cursor = None
            if self.records.events is not None:
                self.records.events.checkpoint(
                    operation_id=attempt.operation_id,
                    payload={
                        "result_ref": ref.model_dump(mode="json"),
                        "phase": phase,
                        "request_epoch": request.request_epoch,
                        "completion": manifest.completion,
                    },
                )
                event = self.records.events.append(
                    project_id=request.project_id,
                    operation_id=attempt.operation_id,
                    event_type="research.terminal" if terminal else "research.progress",
                    payload={
                        "result_ref": ref.model_dump(mode="json"),
                        "phase": phase,
                        "terminal_reason": terminal,
                    },
                )
                event_cursor = event.event_id
            if event_cursor is not None:
                latest = (
                    self.records.journal_read(
                        request.project_id, attempt.operation_id, ResearchAttempt
                    )
                    or attempt
                )
                self.records.journal(
                    request.project_id,
                    attempt.operation_id,
                    latest.model_copy(update={"event_cursor": event_cursor}),
                )
            if terminal is not None:
                self.operations.complete(
                    attempt.operation_id,
                    cast(
                        dict[str, JsonValue],
                        {
                            **result,
                            "contract_version": 2,
                            "thread_id": request.thread_id,
                            "request_epoch": request.request_epoch,
                            "terminal_reason": terminal,
                        },
                    ),
                    completed_at=self.records.clock.now(),
                )
        del work.pending_revisions[: len(staged)]
        return ref

    async def activity_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        from thoth.application.services.conversation_inputs import conversation_inputs

        before = value.get("conversation_before_epoch")
        if before is not None and (type(before) is not int or before < 1):
            raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "invalid conversation cursor")
        base = await self.legacy.activity_list(
            {k: v for k, v in value.items() if k != "conversation_before_epoch"}
        )
        project, thread = str(value["project_id"]), str(value["thread_id"])
        activities = cast(list[JsonValue], base["activities"])
        for revision in self.records.ledger.read_revisions(project, "THREAD", f"request:{thread}"):
            if not self.access.may_read_revision(project, revision.revision_digest):
                continue
            snapshot = self.records.ledger.read_snapshot(revision.snapshot_id)
            if snapshot is None:
                continue
            request = ThreadRequestRevision.model_validate(snapshot.content)
            delivery = self.records.journal_read(
                project, f"input:{request.accepted_input_ids[-1]}", InputDelivery
            )
            activities.append(
                {
                    "activity_id": revision.revision_id,
                    "event_type": "research/inputAccepted",
                    "created_at": revision.created_at.isoformat(),
                    "actor_id": request.actor_ref,
                    "payload": {
                        "request_epoch": request.request_epoch,
                        "authored_text_ref": request.authored_text_ref.model_dump(mode="json"),
                        "input_state": None if delivery is None else delivery.state,
                    },
                }
            )
        return {
            "conversation": conversation_inputs(self.records, self.access, project, thread, before),
            "activities": sorted(
                activities,
                key=lambda item: str(item.get("created_at", "") if isinstance(item, dict) else ""),
            ),
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        base = await self.legacy.read(
            {
                k: v
                for k, v in value.items()
                if k not in {"contract_version", "cursor", "refresh_account_quota"}
            }
        )
        project_id, thread_id = str(value["project_id"]), str(value["thread_id"])
        from thoth.application.services.research_thread_read_snapshot import (
            read_research_snapshot,
        )
        from thoth.application.services.research_usage import summarize_usage
        from thoth.application.services.user_activity_projection import (
            project_user_activity_events,
        )

        snapshot = read_research_snapshot(self, project_id, thread_id)
        if snapshot is None:
            return base
        current_ref, request, attempt = snapshot.current_ref, snapshot.request, snapshot.attempt
        result_ref, manifest = snapshot.result_ref, snapshot.manifest
        currentness, fresh, budget = snapshot.currentness, snapshot.fresh, snapshot.budget
        dispatches, activity_source_context, op = (
            snapshot.dispatches, snapshot.activity_source_context, snapshot.operation
        )
        usage = summarize_usage(
            tuple(dispatches),
            thread_id,
            0 if budget is None else budget.calls,
            account_quota=(
                self.provider_usage.refresh(project_id, thread_id)
                if value.get("refresh_account_quota") is True
                else self.provider_usage.read_snapshot(project_id, thread_id)
            ).model_dump(mode="json"),
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
        from thoth.application.services.research_progress_view import project_activity_events

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
        inconsistent = (
            attempt is not None
            and effective in {"FAILED", "CANCELLED"}
            and attempt.status != effective
        )
        payload = {
            **base,
            **completion,
            "contract_version": 2,
            "request_ref": current_ref.model_dump(mode="json"),
            "request_epoch": request.request_epoch,
            "operation_state": "UNKNOWN" if op is None else op.state.value,
            "operation_error": None if op is None else op.error,
            "failure": None if failure is None else failure.model_dump(mode="json"),
            "cleanup_failure": None
            if cleanup_failure is None
            else cleanup_failure.model_dump(mode="json"),
            "execution_summary": {
                "effective_execution_state": effective,
                "state_inconsistent": inconsistent,
                "last_checkpoint_only": effective in {"FAILED", "CANCELLED"}
                and manifest is not None,
                "automatic_retry": False,
            },
            "request": request.model_dump(mode="json"),
            "attempt": None if attempt is None else attempt.model_dump(mode="json"),
            "current_result": manifest.model_dump(mode="json") if fresh and manifest else None,
            "previous_result": manifest.model_dump(mode="json") if manifest and not fresh else None,
            "freshness": "CURRENT" if fresh else "STALE_OR_PENDING_CURRENT_REQUEST",
            "basis_currentness": currentness.model_dump(mode="json"),
            "cursor": None if attempt is None else attempt.event_cursor,
            "inputs": [
                d.model_dump(mode="json")
                for i in request.accepted_input_ids
                if (d := self.records.journal_read(project_id, f"input:{i}", InputDelivery))
                is not None
            ],
            "budget": None if budget is None else budget.model_dump(mode="json"),
            "usage": usage,
            "model_dispatches": [
                {
                    "dispatch_id": dispatch.dispatch_id,
                    "state": dispatch.state,
                    "received_bytes": dispatch.received_bytes,
                    "remote_stop": dispatch.remote_stop,
                    "transport_observation": None
                    if dispatch.transport_observation is None
                    else dispatch.transport_observation.model_dump(mode="json"),
                }
                for dispatch in dispatches
            ],
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
        from thoth.application.services.research_followup_projection import (
            project_thread_followup,
        )

        progress_summary, coverage_matrix, next_user_action = project_thread_followup(
            ledger=self.records.ledger,
            access=self.access,
            request_ref=current_ref,
            request=request,
            result_ref=result_ref,
            manifest=manifest,
            attempt=attempt,
            operation_state=effective,
            currentness_state=currentness.model_dump(mode="json"),
        )
        payload["user_progress_summary"] = progress_summary.model_dump(mode="json")
        payload["coverage_matrix"] = coverage_matrix.model_dump(mode="json")
        payload["next_user_action"] = next_user_action.model_dump(mode="json")
        payload["activity_events"] = project_activity_events(cast(dict[str, JsonValue], payload))
        payload["user_activity_events"] = project_user_activity_events(
            cast(dict[str, object], payload), source_context=activity_source_context
        )
        return cast(dict[str, JsonValue], payload)
