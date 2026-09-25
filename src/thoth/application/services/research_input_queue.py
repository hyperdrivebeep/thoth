"""Ordered v2 inputs admitted without advancing the current request head."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal, cast

from pydantic import JsonValue

from thoth.domain.auth import authenticated_data_scope_allows, current_authenticated_actor
from thoth.domain.enums import EntityType, OperationState, ProjectLifecycle, ThreadLifecycle
from thoth.domain.model_settings import ModelSelection
from thoth.domain.operation import OperationRecord
from thoth.domain.research_queue import QueuedResearchInput
from thoth.domain.research_request import ResearchAttempt, ThreadRequestRevision
from thoth.ports.model import ModelResolutionError
from thoth.ports.research_queue import ResearchQueueStorePort
from thoth.protocol.deferred import AcceptedRunning, PendingExecution
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


class ResearchInputQueue:
    def __init__(self, store: ResearchQueueStorePort) -> None:
        self.store = store

    def read(self, operation: OperationRecord) -> QueuedResearchInput | None:
        return self.store.read(operation.project_id, operation.operation_id)

    def admission(self, operation: OperationRecord) -> dict[str, JsonValue] | None:
        item = self.read(operation)
        if item is None or item.state == "ACTIVE":
            return None
        return self.accepted_value(item)

    def accepted_value(self, item: QueuedResearchInput) -> dict[str, JsonValue]:
        self_dependency = item.after_operation_id == item.operation_id
        return cast(
            dict[str, JsonValue],
            {
                "contract_version": 2,
                "status": "HOLD" if self_dependency else item.state
                if item.state in {"HOLD", "SUPERSEDED"}
                else "QUEUED_AFTER_CURRENT",
                "project_id": item.project_id,
                "thread_id": item.thread_id,
                "operation_id": item.operation_id,
                "input_id": item.input_id,
                "ordinal": item.ordinal,
                "after_operation_id": item.after_operation_id,
                "input_state": "HOLD" if self_dependency else item.state,
                "hold_reason": "QUEUE_SELF_DEPENDENCY" if self_dependency else item.hold_reason,
                "request_ref": None,
                "recovery": "SAME_KEY_EXPLICIT_RESUBMIT",
            },
        )

    def accept_if_busy(
        self,
        host: ResearchThreadHandlers,
        operation: OperationRecord,
        value: dict[str, JsonValue],
    ) -> AcceptedRunning | PendingExecution | None:
        existing = self.read(operation)
        if existing is not None:
            return self.replay(host, operation, existing)
        project_id, thread_id = str(value["project_id"]), str(value["thread_id"])
        with host.records.ledger.transaction():
            current_item = self.read(operation)
            if current_item is not None:
                return self.replay(host, operation, current_item)
            prior = host.records.read(project_id, EntityType.THREAD, f"request:{thread_id}")
            if prior is None:
                return None
            current_request = ThreadRequestRevision.model_validate(prior[1])
            if current_request.operation_id == operation.operation_id:
                attempt = host.records.journal_read(
                    project_id, operation.operation_id, ResearchAttempt
                )
                if attempt is not None:
                    return host.replay_attempt(attempt, operation)
                return PendingExecution(
                    {
                        "status": "HOLD",
                        "reason": "CURRENT_OPERATION_ATTEMPT_MISSING",
                        "operation_id": operation.operation_id,
                    }
                )
            queued = self.store.list(project_id, thread_id)
            outstanding = tuple(
                item
                for item in queued
                if item.state in {"QUEUED", "HOLD", "ACTIVE"}
                and (running := host.operations.read(item.operation_id)) is not None
                and running.state == OperationState.RUNNING
            )
            if any(item.state == "HOLD" for item in outstanding):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "QUEUE_HELD_REQUIRES_EXPLICIT_RESOLUTION"
                )
            predecessor = (
                outstanding[-1].operation_id if outstanding else current_request.operation_id
            )
            if predecessor == operation.operation_id:
                return PendingExecution(
                    {
                        "status": "HOLD",
                        "reason": "QUEUE_SELF_DEPENDENCY",
                        "operation_id": operation.operation_id,
                    }
                )
            preceding_operation = host.operations.read(predecessor)
            if preceding_operation is None or preceding_operation.state != OperationState.RUNNING:
                return None
            project, thread = host.projects.read(project_id), host.threads.read(thread_id)
            if project is None or thread is None or thread.project_id != project_id:
                raise RpcApplicationError(RpcErrorCode.THREAD_NOT_FOUND, "thread not found")
            if (
                project.lifecycle
                in {
                    ProjectLifecycle.CLOSING,
                    ProjectLifecycle.ARCHIVED_READ_ONLY,
                }
                or thread.lifecycle != ThreadLifecycle.OPEN
            ):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "THREAD_NOT_OPEN")
            if not authenticated_data_scope_allows(thread.scope):
                raise RpcApplicationError(
                    RpcErrorCode.AUTHORIZATION_DENIED, "AUTH_DATA_SCOPE_CHANGED"
                )
            instruction = value.get("instruction")
            if (
                not isinstance(instruction, str)
                or not instruction.strip()
                or len(instruction) > 20_000
            ):
                raise RpcApplicationError(
                    RpcErrorCode.INVALID_PARAMS, "non-empty instruction required"
                )
            edit_kind = value.get("edit_kind", "APPEND")
            if edit_kind not in {"APPEND", "REPLACE"}:
                raise RpcApplicationError(
                    RpcErrorCode.INVALID_PARAMS, "QUEUE_EDIT_KIND_UNSUPPORTED"
                )
            expected_epoch = value.get("expected_request_epoch")
            if edit_kind == "REPLACE" and (
                not isinstance(expected_epoch, int)
                or isinstance(expected_epoch, bool)
                or expected_epoch != current_request.request_epoch
            ):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "REQUEST_EPOCH_CONFLICT")
            policy = host.governance.read_policy(project_id)
            if policy is None:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "POLICY_MISSING")
            try:
                settings = host.model_settings.resolve(
                    project_id,
                    thread_id,
                    ModelSelection.model_validate(
                        {
                            key: value[key]
                            for key in ("provider", "model", "reasoning_effort")
                            if key in value
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
            actor = current_authenticated_actor()
            ordinal = max((item.ordinal for item in queued), default=0) + 1
            item = QueuedResearchInput(
                project_id=project_id,
                thread_id=thread_id,
                operation_id=operation.operation_id,
                idempotency_key=operation.idempotency_key,
                input_id=host.records.ids.new("input"),
                ordinal=ordinal,
                after_operation_id=predecessor,
                accepted_head_digest=prior[0].revision_digest,
                instruction=instruction,
                edit_kind=cast(Literal["APPEND", "REPLACE"], edit_kind),
                continuation=dict(value),
                actor_ref="human:local-user" if actor is None else actor.actor_id,
                owner_session_id=operation.owner_session_id,
                owner_role_assignment_id=operation.owner_role_assignment_id,
                owner_data_scopes=operation.owner_data_scopes,
                scope=dict(thread.scope),
                cutoff_at=project.cutoff_at.isoformat(),
                policy_ref=project.policy_binding_ref,
                policy_digest=policy.policy_digest,
                source_binding_ids=tuple(
                    sorted(
                        binding.binding_id
                        for binding in host.governance.list_source_bindings(project_id)
                        if binding.state == "ACTIVE"
                    )
                ),
                model_settings=settings,
            )
            self.store.save(item)
            if host.records.events is not None:
                host.records.events.append(
                    project_id=project_id,
                    operation_id=operation.operation_id,
                    event_type="research.input_queued",
                    payload={"input_id": item.input_id, "ordinal": ordinal, "after": predecessor},
                )
        return self.replay(host, operation, item)

    def replay(
        self,
        host: ResearchThreadHandlers,
        operation: OperationRecord,
        item: QueuedResearchInput,
    ) -> AcceptedRunning | PendingExecution:
        if item.after_operation_id == item.operation_id:
            return PendingExecution(self.accepted_value(item))
        if item.state in {"HOLD", "SUPERSEDED"}:
            return PendingExecution(self.accepted_value(item))
        if item.state == "ACTIVE":
            attempt = host.records.journal_read(
                item.project_id, operation.operation_id, ResearchAttempt
            )
            if attempt is None:
                return PendingExecution({"status": "ACTIVE_ATTEMPT_UNAVAILABLE"})
            return host.replay_attempt(attempt, operation)
        return AcceptedRunning(
            self.accepted_value(item),
            lambda: self.wait_then_run(host, operation.operation_id),
        )

    async def wait_then_run(
        self, host: ResearchThreadHandlers, operation_id: str
    ) -> dict[str, JsonValue] | PendingExecution:
        operation = host.operations.read(operation_id)
        if operation is None:
            return PendingExecution({"status": "QUEUE_OPERATION_UNAVAILABLE"})
        try:
            while True:
                item = self.read(operation)
                if item is None:
                    return PendingExecution({"status": "QUEUE_RECORD_UNAVAILABLE"})
                if item.after_operation_id == item.operation_id:
                    return PendingExecution(self.accepted_value(item))
                if item.state in {"HOLD", "SUPERSEDED"}:
                    return PendingExecution(self.accepted_value(item))
                if item.state == "ACTIVE":
                    replayed = self.replay(host, operation, item)
                    if isinstance(replayed, AcceptedRunning):
                        return await replayed.continue_execution()
                    return replayed
                predecessor = host.operations.read(item.after_operation_id)
                if predecessor is None or predecessor.state in {
                    OperationState.FAILED,
                    OperationState.CANCELLED,
                }:
                    held = self.hold(host, item, "PREDECESSOR_NOT_SUCCEEDED")
                    return PendingExecution(self.accepted_value(held))
                if predecessor.state == OperationState.SUCCEEDED:
                    return await self._activate_and_run(host, operation, item)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            current = host.operations.read(operation_id)
            if current is not None and current.state == OperationState.CANCELLED:
                item = self.read(current)
                if item is not None and item.state == "QUEUED":
                    held = self.hold(host, item, "QUEUE_OPERATION_CANCELLED")
                    return PendingExecution(self.accepted_value(held))
            # Runtime shutdown is not user cancellation. Preserve the durable queue and
            # require an explicit same-key replay after restart.
            return PendingExecution(
                {"status": "QUEUED_AFTER_CURRENT", "operation_id": operation_id}
            )

    async def _activate_and_run(
        self,
        host: ResearchThreadHandlers,
        operation: OperationRecord,
        item: QueuedResearchInput,
    ) -> dict[str, JsonValue] | PendingExecution:
        with host.records.ledger.transaction():
            latest = self.read(operation)
            if latest is None or latest.state != "QUEUED":
                return PendingExecution({"status": "QUEUE_STATE_CHANGED"})
            if latest.after_operation_id == latest.operation_id:
                return PendingExecution(self.accepted_value(latest))
            current_operation = host.operations.read(operation.operation_id)
            if current_operation is None or current_operation.state != OperationState.RUNNING:
                return PendingExecution({"status": "QUEUE_OPERATION_NOT_RUNNING"})
            predecessor = host.operations.read(latest.after_operation_id)
            if predecessor is None or predecessor.state != OperationState.SUCCEEDED:
                return PendingExecution({"status": "QUEUE_PREDECESSOR_CHANGED"})
            head = host.records.read(
                latest.project_id, EntityType.THREAD, f"request:{latest.thread_id}"
            )
            if head is None:
                held = self.hold(host, latest, "REQUEST_HEAD_UNAVAILABLE")
                return PendingExecution(self.accepted_value(held))
            previous_queue = self.store.read(latest.project_id, latest.after_operation_id)
            expected_digest = (
                latest.accepted_head_digest
                if previous_queue is None
                else None
                if previous_queue.activated_request_ref is None
                else previous_queue.activated_request_ref.revision_digest
            )
            if head[0].revision_digest != expected_digest:
                held = self.hold(host, latest, "REQUEST_HEAD_CHANGED")
                return PendingExecution(self.accepted_value(held))
            current_request = ThreadRequestRevision.model_validate(head[1])
            if (
                latest.edit_kind == "REPLACE"
                and latest.continuation.get("expected_request_epoch")
                != current_request.request_epoch
            ):
                held = self.hold(host, latest, "REQUEST_EPOCH_CHANGED")
                return PendingExecution(self.accepted_value(held))
            result = host.records.read(
                latest.project_id, EntityType.DECISION_OBJECT, f"result:{latest.thread_id}"
            )
            if (
                result is None
                or result[1].get("operation_id") != latest.after_operation_id
                or result[1].get("completion") != "TERMINAL"
            ):
                held = self.hold(host, latest, "PREDECESSOR_TERMINAL_RESULT_UNAVAILABLE")
                return PendingExecution(self.accepted_value(held))
            reason = self._basis_change(host, latest)
            if reason is not None:
                held = self.hold(host, latest, reason)
                return PendingExecution(self.accepted_value(held))
            accepted = host.submit(
                dict(latest.continuation),
                queued_input_id=latest.input_id,
            )
            attempt = host.records.journal_read(
                latest.project_id, operation.operation_id, ResearchAttempt
            )
            if attempt is None:
                raise ValueError("QUEUE_ACTIVATION_ATTEMPT_MISSING")
            self.store.save(
                latest.model_copy(
                    update={"state": "ACTIVE", "activated_request_ref": attempt.request_ref}
                )
            )
        return await accepted.continue_execution()

    def _basis_change(self, host: ResearchThreadHandlers, item: QueuedResearchInput) -> str | None:
        project, thread = host.projects.read(item.project_id), host.threads.read(item.thread_id)
        if project is None or thread is None:
            return "PROJECT_OR_THREAD_UNAVAILABLE"
        if (
            project.lifecycle
            in {
                ProjectLifecycle.CLOSING,
                ProjectLifecycle.ARCHIVED_READ_ONLY,
            }
            or thread.lifecycle != ThreadLifecycle.OPEN
        ):
            return "PROJECT_OR_THREAD_CLOSED"
        if thread.scope != item.scope or not authenticated_data_scope_allows(thread.scope):
            return "THREAD_SCOPE_CHANGED"
        if (
            project.cutoff_at.isoformat() != item.cutoff_at
            or project.policy_binding_ref != item.policy_ref
        ):
            return "PROJECT_BASIS_CHANGED"
        policy = host.governance.read_policy(item.project_id)
        if policy is None or policy.policy_digest != item.policy_digest:
            return "PROJECT_POLICY_CHANGED"
        active_bindings = {
            binding.binding_id
            for binding in host.governance.list_source_bindings(item.project_id)
            if binding.state == "ACTIVE"
        }
        if set(item.source_binding_ids) != active_bindings:
            return "SOURCE_BINDING_BASIS_CHANGED"
        actor = current_authenticated_actor()
        if item.owner_session_id is not None:
            if (
                actor is None
                or actor.actor_id != item.actor_ref
                or actor.session_id != item.owner_session_id
            ):
                return "QUEUE_OWNER_CHANGED"
            role = next(
                (
                    role
                    for role in host.governance.list_roles(item.project_id)
                    if role.role_assignment_id == item.owner_role_assignment_id
                ),
                None,
            )
            if role is None or role.state != "ACTIVE" or role.actor_id != actor.actor_id:
                return "QUEUE_AUTHORITY_REVOKED"
        try:
            selected = host.model_settings.resolve(
                item.project_id,
                item.thread_id,
                ModelSelection.model_validate(
                    {
                        key: item.continuation[key]
                        for key in ("provider", "model", "reasoning_effort")
                        if key in item.continuation
                    }
                ),
            )
        except ModelResolutionError:
            return "MODEL_SETTINGS_UNAVAILABLE"
        if selected != item.model_settings:
            return "MODEL_SETTINGS_CHANGED"
        return None

    def hold(
        self, host: ResearchThreadHandlers, item: QueuedResearchInput, reason: str
    ) -> QueuedResearchInput:
        with host.records.ledger.transaction():
            current = self.store.read(item.project_id, item.operation_id)
            if current is None or current.state != "QUEUED":
                return item if current is None else current
            held = current.model_copy(update={"state": "HOLD", "hold_reason": reason})
            self.store.save(held)
            return held

    def hold_for_steer(self, host: ResearchThreadHandlers, project_id: str, thread_id: str) -> None:
        for item in self.store.list(project_id, thread_id):
            if item.state in {"QUEUED", "HOLD"}:
                self.store.save(
                    item.model_copy(
                        update={"state": "SUPERSEDED", "hold_reason": "STEER_CHANGED_DIRECTION"}
                    )
                )

    def cancelled(self, project_id: str, operation_id: str) -> None:
        item = self.store.read(project_id, operation_id)
        if item is None or item.state not in {"QUEUED", "HOLD"}:
            return
        self.store.save(
            item.model_copy(update={"state": "HOLD", "hold_reason": "QUEUE_OPERATION_CANCELLED"})
        )

    def reconcile_terminal_operations(self, host: ResearchThreadHandlers) -> None:
        """Repair a crash between operation cancellation and queue disposition."""
        for project in host.projects.list():
            with host.records.ledger.transaction():
                for item in self.store.list_project(project.project_id):
                    if item.state != "QUEUED":
                        continue
                    if item.after_operation_id == item.operation_id:
                        continue
                    operation = host.operations.read(item.operation_id)
                    if operation is None or operation.state not in {
                        OperationState.CANCELLED,
                        OperationState.FAILED,
                    }:
                        continue
                    self.store.save(
                        item.model_copy(
                            update={
                                "state": "HOLD",
                                "hold_reason": f"QUEUE_OPERATION_{operation.state.value}",
                            }
                        )
                    )

    def summary(self, project_id: str, thread_id: str) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            {
                "input_id": item.input_id,
                "operation_id": item.operation_id,
                "ordinal": item.ordinal,
                "state": "HOLD" if item.after_operation_id == item.operation_id else item.state,
                "hold_reason": (
                    "QUEUE_SELF_DEPENDENCY"
                    if item.after_operation_id == item.operation_id
                    else item.hold_reason
                ),
                "after_operation_id": item.after_operation_id,
            }
            for item in self.store.list(project_id, thread_id)
            if item.state in {"QUEUED", "HOLD", "SUPERSEDED"}
        )
