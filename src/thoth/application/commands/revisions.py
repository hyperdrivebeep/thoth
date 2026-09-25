from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services import (
    BaselineService,
    RestoreService,
    RevisionCommitService,
    semantic_diff,
)
from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.errors import InvariantViolation
from thoth.domain.revision import RevisionComparison
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class RevisionEntityInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=160)


class RevisionCompareInput(RevisionEntityInput):
    left_revision_id: str = Field(min_length=1, max_length=160)
    right_revision_id: str = Field(min_length=1, max_length=160)


class RevisionRestoreInput(RevisionEntityInput):
    selected_revision_id: str = Field(min_length=1, max_length=160)
    expected_current_head: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=2_000)
    actor_id: str = Field(default="human:cli", min_length=1, max_length=160)
    actor_role: str = Field(default="project-owner", min_length=1, max_length=160)


class RevisionCommandHandlers:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        dependencies: DependencyGraphPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        baselines: BaselineService | None = None,
        policy_version: str = "policy:default",
    ) -> None:
        self._ledger = ledger
        self._dependencies = dependencies
        self._clock = clock
        self._ids = ids
        self._policy_version = policy_version
        self._baselines = baselines

    async def history(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevisionEntityInput.model_validate(value)
        revisions = self._ledger.read_revisions(
            request.project_id, request.entity_type.value, request.entity_id
        )
        return {"revisions": [revision.model_dump(mode="json") for revision in revisions]}

    async def compare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevisionCompareInput.model_validate(value)
        revisions = self._ledger.read_revisions(
            request.project_id, request.entity_type.value, request.entity_id
        )
        by_id = {revision.revision_id: revision for revision in revisions}
        left = by_id.get(request.left_revision_id)
        right = by_id.get(request.right_revision_id)
        if left is None or right is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "revision comparison references an unknown revision",
            )
        left_snapshot = self._ledger.read_snapshot(left.snapshot_id)
        right_snapshot = self._ledger.read_snapshot(right.snapshot_id)
        if left_snapshot is None or right_snapshot is None:
            raise RpcApplicationError(
                RpcErrorCode.INTERNAL_ERROR,
                "revision snapshot is missing",
            )
        comparison = RevisionComparison(
            project_id=request.project_id,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            left_revision_id=left.revision_id,
            right_revision_id=right.revision_id,
            changes=semantic_diff(left_snapshot.content, right_snapshot.content),
        )
        return {"comparison": comparison.model_dump(mode="json")}

    async def restore(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevisionRestoreInput.model_validate(value)
        service = RestoreService(
            ledger=self._ledger,
            dependencies=self._dependencies,
            commits=RevisionCommitService(
                self._ledger,
                self._clock,
                self._ids,
                policy_version=self._policy_version,
            ),
            clock=self._clock,
            ids=self._ids,
        )
        try:
            result = service.restore_as_new_revision(
                project_id=request.project_id,
                entity_type=request.entity_type,
                entity_id=request.entity_id,
                selected_historical_revision_id=request.selected_revision_id,
                expected_current_head=request.expected_current_head,
                actor=ActorRef(
                    actor_id=request.actor_id,
                    kind=ActorKind.HUMAN,
                    role=request.actor_role,
                ),
                reason=request.reason,
            )
        except InvariantViolation as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        if self._baselines is not None:
            self._baselines.refresh(
                project_id=request.project_id,
                thread_id=None,
                purpose="Restore baseline invalidation",
                propose_candidates=False,
            )
        return {"restore": result.model_dump(mode="json")}
