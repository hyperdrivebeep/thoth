"""Server-only, task-local policy for verification of an already sealed query envelope."""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.operation import OperationRecord
from thoth.domain.research_codec import decode_current_result_manifest
from thoth.domain.research_execution import research_work
from thoth.domain.research_request import CurrentResultPayload, RevisionRef, ThreadRequestRevision
from thoth.domain.resource_scope import ResourceScopeError, ResourceUse, current_resource_uses
from thoth.ports.ledger import LedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.resource_scope import HistoricalOperationVerifierPort, ResourceAccessPort
from thoth.protocol.deferred import current_operation


@dataclass
class QueryVerification:
    task: asyncio.Task[object] | None
    enabled: bool
    active: bool = True


_QUERY: ContextVar[QueryVerification | None] = ContextVar(
    "historical_query_verification", default=None
)


@contextmanager
def historical_query_verification() -> Generator[None]:
    """Only CommandBus.query enters this policy, before preclaim and handler execution."""
    state = QueryVerification(
        asyncio.current_task(),
        current_operation.get() is None
        and research_work.get() is None
        and current_resource_uses() is None,
    )
    token = _QUERY.set(state)
    try:
        yield
    finally:
        state.active = False
        _QUERY.reset(token)


def historical_verification_allowed() -> bool:
    state = _QUERY.get()
    if state is None or not state.enabled or not state.active:
        return False
    try:
        return (
            state.task is asyncio.current_task()
            and current_operation.get() is None
            and research_work.get() is None
        )
    except RuntimeError:
        return False


def require_historical_operation_access(
    access: ResourceAccessPort, operation: OperationRecord
) -> None:
    if historical_verification_allowed() and isinstance(access, HistoricalOperationVerifierPort):
        access.verify_historical_operation_access(operation)
    else:
        access.require_operation(operation)


def require_historical_manifest_access(
    access: ResourceAccessPort,
    ledger: LedgerPort,
    operations: OperationStorePort,
    ref: RevisionRef,
    manifest: CurrentResultPayload,
    thread_id: str,
) -> None:
    """A progress manifest is independently immutable even before operation termination."""
    operation = operations.read(manifest.operation_id)
    if operation is None:
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
    project = operation.project_id
    revision = ledger.read_revision_by_digest(project, ref.revision_digest)
    snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
    if (
        revision is None
        or snapshot is None
        or ref.project_id != project
        or (revision.entity_type.value, revision.entity_id, revision.revision_id)
        != (ref.entity_type, ref.entity_id, ref.revision_id)
        or ref.entity_type != "DECISION_OBJECT"
        or ref.entity_id != f"result:{thread_id}"
        or snapshot.project_id != project
        or snapshot.entity_id != ref.entity_id
        or snapshot.entity_type.value != ref.entity_type
        or snapshot.content_digest
        != domain_digest("SNAPSHOT", snapshot.schema_version, canonical_payload(snapshot.content))
    ):
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
    if (
        decode_current_result_manifest(dict(snapshot.content)) != manifest
        or manifest.operation_id != operation.operation_id
    ):
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
    request_ref = manifest.request_ref
    request_revision = ledger.read_revision_by_digest(project, request_ref.revision_digest)
    request_snapshot = (
        None if request_revision is None else ledger.read_snapshot(request_revision.snapshot_id)
    )
    if (
        request_ref.project_id != project
        or request_revision is None
        or request_snapshot is None
        or request_ref.entity_type != "THREAD"
        or request_ref.entity_id != f"request:{thread_id}"
        or (
            request_revision.entity_type.value,
            request_revision.entity_id,
            request_revision.revision_id,
        )
        != (request_ref.entity_type, request_ref.entity_id, request_ref.revision_id)
        or request_snapshot.project_id != project
        or request_snapshot.entity_id != request_ref.entity_id
    ):
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
    request = ThreadRequestRevision.model_validate(request_snapshot.content)
    if (
        request.project_id != project
        or request.thread_id != thread_id
        or request.operation_id != operation.operation_id
    ):
        raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
    access.require_reads(
        project, (f"revision:{ref.revision_digest}", f"revision:{request_ref.revision_digest}")
    )
    # This is an access-check envelope only; no operation/result is mutated or published.
    require_historical_operation_access(
        access,
        operation.model_copy(
            update={
                "result": manifest.result,
                "error": None,
                "resource_uses": tuple(
                    ResourceUse.model_validate(use) for use in manifest.resource_uses
                ),
            }
        ),
    )
