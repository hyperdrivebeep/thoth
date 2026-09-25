"""Bind new revision proposals and audit projections to their actual source reads."""

from typing import cast

from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.control_record import ControlRecord
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort


class ScopedRevisionControls:
    def __init__(
        self, raw: ControlRecordStorePort, ledger: LedgerPort, scopes: ResourceScopeService
    ) -> None:
        self._raw = raw
        self._ledger = ledger
        self._scopes = scopes

    def append(self, value: ControlRecord) -> None:
        if value.namespace != "REVISION":
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        explicit = list(self._strings(value.payload.get("evidence_refs", ())))
        explicit.extend(
            f"revision:{ref}"
            for ref in self._strings(value.payload.get("parent_revision_digests", ()))
        )
        if value.supersedes_digest is not None:
            explicit.append(f"control:{value.supersedes_digest}")
        with self._ledger.transaction():
            # Check draft access before the lineage writer drops source-free provenance.
            self._scopes.require_reads(value.project_id, tuple(explicit))
            parents = (
                *explicit,
                *(
                    use.resource_ref
                    for use in current_resource_uses() or ()
                    if use.project_id == value.project_id and use.capability == "READ"
                ),
            )
            self._raw.append(value)
            self._scopes.record_control_lineage(value.project_id, value.record_digest, parents)

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        refs = cast(tuple[object, ...] | list[object], value)
        if any(not isinstance(ref, str) for ref in refs):
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        return tuple(str(ref) for ref in refs)

    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None:
        item = self._raw.read(project_id, namespace, record_id)
        if item is not None:
            self._scopes.require_read(project_id, f"control:{item.record_digest}")
        return item

    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None:
        item = self._raw.read_digest(project_id, digest)
        if item is not None:
            self._scopes.require_read(project_id, f"control:{item.record_digest}")
        return item

    def list(
        self,
        project_id: str,
        namespace: str,
        record_type: str | None = None,
        *,
        latest_only: bool = True,
    ) -> tuple[ControlRecord, ...]:
        return tuple(
            item
            for item in self._raw.list(project_id, namespace, record_type, latest_only=latest_only)
            if self._scopes.may_read(project_id, f"control:{item.record_digest}")
        )
