"""Durable total reservation and current authority checks around bounded I/O."""

import hashlib
from collections.abc import Callable

from pydantic import ValidationError

from thoth.application.services.request_records import RequestRecords
from thoth.domain.auth import authenticated_data_scope_allows, current_authenticated_actor
from thoth.domain.enums import EntityType, OperationState, ProjectLifecycle, ThreadLifecycle
from thoth.domain.model_dispatch import (
    ModelControlCapability,
    ModelDispatchRecord,
    ModelReceiveObservation,
)
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchFence
from thoth.domain.research_lease import ResearchLeaseLost, ResearchPaused
from thoth.domain.research_request import ResearchBudget, ThreadRequestRevision
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.model import ModelExecutionHold
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.thread import ThreadStorePort


class RequestBoundary:
    def __init__(
        self,
        records: RequestRecords,
        projects: ProjectStorePort,
        threads: ThreadStorePort,
        governance: GovernanceStorePort,
        operations: OperationStorePort,
        operation: OperationRecord,
        request: ThreadRequestRevision,
        *,
        post_effect_learning: bool = False,
    ) -> None:
        self.records, self.projects, self.threads = records, projects, threads
        self.governance, self.operations = governance, operations
        self.operation, self.request = operation, request
        self.post_effect_learning = post_effect_learning
        self.required_source_bindings = frozenset(
            binding.binding_id
            for binding in governance.list_source_bindings(request.project_id)
            if binding.state == "ACTIVE"
        )
        self.validate_sources: Callable[[], None] | None = None
        self.validate_owner: Callable[[], None] | None = None

    def owns_attempt(self) -> bool:
        try:
            if self.validate_owner is not None:
                self.validate_owner()
            return True
        except ResearchLeaseLost:
            return False

    def check(self) -> None:
        if self.validate_owner is not None:
            self.validate_owner()
        request = self.request
        head = self.records.read(
            request.project_id, EntityType.THREAD, f"request:{request.thread_id}"
        )
        if head is None or head[1].get("request_epoch") != request.request_epoch:
            raise ResearchFence("REQUEST_SUPERSEDED")
        op = self.operations.read(self.operation.operation_id)
        thread = self.threads.read(request.thread_id)
        if (
            op is None
            or (
                op.state != OperationState.RUNNING
                and not (self.post_effect_learning and op.state == OperationState.SUCCEEDED)
            )
            or thread is None
            or thread.lifecycle != ThreadLifecycle.OPEN
        ):
            raise ResearchFence("CANCELLED_OR_THREAD_STOPPED")
        if thread.execution_state.value in {"PAUSED", "PAUSE_PENDING"}:
            raise ResearchPaused("PAUSED_WITH_CHECKPOINT")
        project = self.projects.read(request.project_id)
        if (
            project is None
            or project.cutoff_at.isoformat() != request.cutoff_at
            or project.policy_binding_ref != request.policy_ref
            or project.lifecycle in {ProjectLifecycle.CLOSING, ProjectLifecycle.ARCHIVED_READ_ONLY}
        ):
            raise ResearchFence("PROJECT_BASIS_CHANGED")
        if not authenticated_data_scope_allows(thread.scope):
            raise PermissionError("AUTH_DATA_SCOPE_CHANGED")
        policy = self.governance.read_policy(request.project_id)
        if policy is None or policy.policy_digest != request.policy_digest:
            raise ResearchFence("PROJECT_POLICY_REVISION_CHANGED")
        active_source_bindings = {
            binding.binding_id
            for binding in self.governance.list_source_bindings(request.project_id)
            if binding.state == "ACTIVE"
        }
        if not self.required_source_bindings <= active_source_bindings:
            raise ResearchFence("SOURCE_BINDING_REVOKED")
        if self.validate_sources is not None:
            self.validate_sources()
        actor = current_authenticated_actor()
        if self.operation.owner_actor_id is not None:
            if (
                actor is None
                or actor.actor_id != self.operation.owner_actor_id
                or actor.session_id != self.operation.owner_session_id
            ):
                raise PermissionError("RESEARCH_OWNER_CHANGED")
            role = next(
                (
                    r
                    for r in self.governance.list_roles(request.project_id)
                    if r.role_assignment_id == actor.role_assignment_id
                ),
                None,
            )
            if role is None or role.state != "ACTIVE" or role.actor_id != actor.actor_id:
                raise PermissionError("RESEARCH_AUTHORITY_REVOKED")

    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None:
        """Reserve non-model I/O, retaining its operational payload bound."""
        with self.records.ledger.transaction():
            self.transport(payload_bytes)
            self._reserve_call(payload_bytes, output_tokens)

    def _reserve_call(self, payload_bytes: int, output_tokens: int) -> None:
        self.check()
        project, key = self.request.project_id, f"budget:{self.request.thread_id}"
        with self.records.ledger.transaction():
            budget = self.records.journal_read(project, key, ResearchBudget)
            if budget is None:
                raise ModelExecutionHold("RESEARCH_BUDGET_MISSING")
            # Preserve legacy accounting units; bytes are not an exact input-token count.
            reserved = payload_bytes + output_tokens
            self.records.journal(
                project,
                key,
                budget.model_copy(
                    update={
                        "calls": budget.calls + 1,
                        "reserved_tokens": budget.reserved_tokens + reserved,
                    }
                ),
            )

    def new_model_call(self) -> str:
        self.check()
        return self.records.ids.new("model-call")

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        self.check()
        if capability.output_control == "UNVERIFIED" or capability.native_tools != "NONE":
            raise ModelExecutionHold("MODEL_TRANSPORT_CONTROLS_UNVERIFIED")
        record = ModelDispatchRecord(
            dispatch_id=dispatch_id,
            thread_id=self.request.thread_id,
            operation_id=self.request.operation_id,
            capability=capability,
            payload_bytes=len(payload),
            payload_digest=hashlib.sha256(payload).hexdigest(),
            output_reserved=output_tokens,
            model_settings=self.request.model_settings,
        )
        project = self.request.project_id
        with self.records.ledger.transaction():
            prior = self.records.journal_read(project, dispatch_id, ModelDispatchRecord)
            if prior is not None:
                if prior.thread_id not in {
                    None,
                    self.request.thread_id,
                } or prior.operation_id not in {None, self.request.operation_id}:
                    raise ModelExecutionHold("DISPATCH_IDENTITY_CONFLICT")
                if (prior.payload_digest, prior.output_reserved) != (
                    record.payload_digest,
                    output_tokens,
                ):
                    raise ModelExecutionHold("DISPATCH_IDENTITY_CONFLICT")
                return
            # Model context capacity is not the legacy operational byte limit.
            # Still reserve every actual dispatch (including repair) under this attempt.
            self._reserve_call(len(payload), output_tokens)
            self.records.journal(project, dispatch_id, record)

    def record_usage(
        self,
        dispatch_id: str,
        received_bytes: int,
        input_tokens: int | None,
        output_tokens: int | None,
        remote_stop: str,
        response_id: str | None,
        observation: ModelReceiveObservation | None = None,
        retry_of_dispatch_id: str | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        project = self.request.project_id
        with self.records.ledger.transaction():
            prior = self.records.journal_read(project, dispatch_id, ModelDispatchRecord)
            if prior is None:
                raise ValueError("MODEL_DISPATCH_RESERVATION_MISSING")
            if prior.thread_id not in {None, self.request.thread_id} or prior.operation_id not in {
                None,
                self.request.operation_id,
            }:
                raise ModelExecutionHold("DISPATCH_IDENTITY_CONFLICT")
            self.records.journal(
                project,
                dispatch_id,
                prior.model_copy(
                    update={
                        "thread_id": self.request.thread_id,
                        "operation_id": self.request.operation_id,
                        "state": "OBSERVED",
                        "received_bytes": received_bytes,
                        "input_tokens": prior.input_tokens
                        if input_tokens is None
                        else input_tokens,
                        "output_tokens": prior.output_tokens
                        if output_tokens is None
                        else output_tokens,
                        "remote_stop": remote_stop,
                        "response_id": response_id,
                        "transport_observation": observation,
                        "retry_of_dispatch_id": prior.retry_of_dispatch_id or retry_of_dispatch_id,
                        "cached_input_tokens": prior.cached_input_tokens
                        if cached_input_tokens is None
                        else cached_input_tokens,
                    }
                ),
            )

    def call_timeout(self) -> float | None:
        budget = self.records.journal_read(
            self.request.project_id, f"budget:{self.request.thread_id}", ResearchBudget
        )
        if budget is None:
            raise ModelExecutionHold("RESEARCH_BUDGET_MISSING")
        # Usage is feedback for the agent, not an arbitrary total research deadline.
        # Preserve historical max_seconds/max_calls fields without reactivating them.
        return None

    def usage_observation(self) -> dict[str, object]:
        from thoth.application.services.research_usage import summarize_usage

        try:
            budget = self.records.journal_read(
                self.request.project_id, f"budget:{self.request.thread_id}", ResearchBudget
            )
        except ValidationError:
            budget = None
        records: list[ModelDispatchRecord] = []
        try:
            listed = self.records.controls.list(
                self.request.project_id,
                "RESEARCH_EXECUTION",
                "ModelDispatchRecord",
                latest_only=True,
            )
        except ValidationError:
            listed = ()
        for record in listed:
            try:
                records.append(ModelDispatchRecord.model_validate(record.payload))
            except ValidationError:
                continue
        return dict(
            summarize_usage(
                records,
                self.request.thread_id,
                0 if budget is None else budget.calls,
            )
        )

    def transport(self, payload_bytes: int) -> None:
        self.check()
        budget = self.records.journal_read(
            self.request.project_id, f"budget:{self.request.thread_id}", ResearchBudget
        )
        if budget is None or payload_bytes > budget.max_prompt_bytes:
            raise ModelExecutionHold("SERIALIZED_MODEL_INPUT_LIMIT")
