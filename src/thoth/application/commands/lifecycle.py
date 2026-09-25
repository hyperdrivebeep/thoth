from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services import CommitResult, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.closure import Closure, Export
from thoth.domain.enums import (
    ActorKind,
    ClosureStatus,
    EntityType,
    ExportReleaseState,
    ReceiptClaimScope,
    ReceiptType,
)
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ClosurePrepareInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str | None = Field(default=None, max_length=160)
    resolution: str = Field(min_length=1, max_length=10_000)
    unresolved_refs: tuple[str, ...] = ()
    open_effect_refs: tuple[str, ...] = ()
    retention_policy_ref: str = Field(
        default="retention:local-reference-v1", min_length=1, max_length=260
    )


class ClosureReadInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    closure_id: str = Field(min_length=1, max_length=160)


class ClosureFinalizeInput(ClosureReadInput):
    expected_current_head: str = Field(min_length=64, max_length=64)


class ExportPrepareInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=2_000)
    audience: str = Field(min_length=1, max_length=1_000)


class ProjectScopedInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class LifecycleCommandHandlers:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        artifacts: ArtifactLedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        unit_of_work: AtomicUnitOfWorkPort,
        policy_version: str = "policy:local-default-v1",
    ) -> None:
        self._ledger = ledger
        self._artifacts = artifacts
        self._clock = clock
        self._ids = ids
        self._unit_of_work = unit_of_work
        self._commits = RevisionCommitService(
            ledger,
            clock,
            ids,
            policy_version=policy_version,
        )

    async def prepare_closure(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = ClosurePrepareInput.model_validate(value)
            closure_id = f"closure:{request.thread_id or request.project_id}"
            before_heads = dict(self._ledger.read_heads(request.project_id))
            current = before_heads.get(f"CLOSURE:{closure_id}")
            status = (
                ClosureStatus.BLOCKED
                if request.unresolved_refs or request.open_effect_refs
                else ClosureStatus.READY
            )
            closure = Closure(
                closure_id=closure_id,
                project_id=request.project_id,
                thread_id=request.thread_id,
                status=status,
                resolution=request.resolution,
                unresolved_refs=request.unresolved_refs,
                open_effect_refs=request.open_effect_refs,
                retention_policy_ref=request.retention_policy_ref,
                actor=self._actor(),
                head_set_digest=head_set_digest(before_heads),
            )
            commit = self._commit_entity(
                request.project_id,
                EntityType.CLOSURE,
                closure_id,
                closure.model_dump(mode="python"),
                reason="prepare local project closure",
                expected={} if current is None else {f"CLOSURE:{closure_id}": current},
                receipt_type=ReceiptType.CLOSURE,
                claim_scope=ReceiptClaimScope.CLOSURE_DECISION_RECORDED,
            )
            return {
                "closure": closure.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def read_closure(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        closure, head = self._current_closure(request.project_id, request.closure_id)
        return {"closure": closure.model_dump(mode="json"), "head_digest": head}

    async def finalize_closure(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = ClosureFinalizeInput.model_validate(value)
            closure, head = self._current_closure(request.project_id, request.closure_id)
            if head != request.expected_current_head:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "closure changed after the finalize preview",
                )
            if closure.status != ClosureStatus.READY:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "only a READY closure can be finalized",
                )
            closed = closure.model_copy(
                update={
                    "status": ClosureStatus.CLOSED,
                    "closed_at": self._clock.now(),
                    "actor": self._actor(),
                    "head_set_digest": head_set_digest(self._ledger.read_heads(request.project_id)),
                }
            )
            commit = self._commit_entity(
                request.project_id,
                EntityType.CLOSURE,
                request.closure_id,
                closed.model_dump(mode="python"),
                reason="finalize local project closure",
                expected={f"CLOSURE:{request.closure_id}": head},
                receipt_type=ReceiptType.CLOSURE,
                claim_scope=ReceiptClaimScope.CLOSURE_DECISION_RECORDED,
            )
            return {
                "closure": closed.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def prepare_export(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = ExportPrepareInput.model_validate(value)
            before_heads = dict(self._ledger.read_heads(request.project_id))
            export_id = self._ids.new("export")
            export = Export(
                export_id=export_id,
                project_id=request.project_id,
                purpose=request.purpose,
                audience=request.audience,
                head_set_digest=head_set_digest(before_heads),
                artifact_refs=tuple(
                    artifact.artifact_id
                    for artifact in self._artifacts.list_artifacts(request.project_id)
                ),
                receipt_refs=tuple(
                    receipt.receipt_id for receipt in self._ledger.read_receipts(request.project_id)
                ),
                release_state=ExportReleaseState.LOCAL_SEALED,
                prepared_by=self._actor(),
            )
            commit = self._commit_entity(
                request.project_id,
                EntityType.EXPORT,
                export_id,
                export.model_dump(mode="python"),
                reason="prepare purpose-bound local export",
                expected={},
                receipt_type=ReceiptType.EXPORT,
                claim_scope=ReceiptClaimScope.EXPORT_SNAPSHOT_RECORDED,
            )
            return {
                "export": export.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def list_exports(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectScopedInput.model_validate(value)
        values: list[JsonValue] = []
        for key, digest in sorted(self._ledger.read_heads(request.project_id).items()):
            if not key.startswith("EXPORT:"):
                continue
            revision = self._ledger.read_revision_by_digest(request.project_id, digest)
            snapshot = (
                None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
            )
            if snapshot is None:
                continue
            values.append(Export.model_validate(snapshot.content).model_dump(mode="json"))
        return {"exports": values}

    def _commit_entity(
        self,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        content: dict[str, object],
        *,
        reason: str,
        expected: dict[str, str],
        receipt_type: ReceiptType,
        claim_scope: ReceiptClaimScope,
    ) -> CommitResult:
        snapshot_digest = domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content))
        parents = tuple(expected.values())
        revision_payload = {
            "project_id": project_id,
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "content_digest": snapshot_digest,
            "parents": parents,
            "reason": reason,
        }
        revision = SemanticRevision(
            revision_id=self._ids.new("revision"),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            snapshot_id=self._ids.new("snapshot"),
            parent_revision_digests=parents,
            actor=self._actor(),
            reason=reason,
            evidence_refs=(),
            affected_refs=(),
            revision_digest=domain_digest(
                "SEMANTIC_REVISION", "1.0.0", canonical_payload(revision_payload)
            ),
            created_at=self._clock.now(),
        )
        snapshot = EntitySnapshot(
            snapshot_id=revision.snapshot_id,
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            schema_version="1.0.0",
            content=content,
            content_digest=snapshot_digest,
        )
        return self._commits.commit(
            RevisionChangeSet(
                changeset_id=self._ids.new("changeset"),
                project_id=project_id,
                expected_heads=expected,
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(),
                actor=self._actor(),
                reason=reason,
                receipt_type=receipt_type,
                receipt_claim_scopes=(
                    claim_scope,
                    ReceiptClaimScope.ARTIFACT_INTEGRITY,
                    ReceiptClaimScope.PROVENANCE_BOUND,
                ),
            )
        )

    def _current_closure(self, project_id: str, closure_id: str) -> tuple[Closure, str]:
        head = self._ledger.read_heads(project_id).get(f"CLOSURE:{closure_id}")
        if head is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "closure was not found")
        revision = self._ledger.read_revision_by_digest(project_id, head)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            raise RpcApplicationError(RpcErrorCode.INTERNAL_ERROR, "closure snapshot is missing")
        return Closure.model_validate(snapshot.content), head

    @staticmethod
    def _actor() -> ActorRef:
        return ActorRef(
            actor_id="human:web-user",
            kind=ActorKind.HUMAN,
            role="project-owner",
        )
