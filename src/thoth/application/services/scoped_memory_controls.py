"""Current source access for the public, noncanonical memory control-record surface."""

from __future__ import annotations

from typing import cast

from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.control_record import ControlRecord
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.resource_scope import ResourceAccessPort


class ScopedMemoryControls:
    def __init__(
        self, raw: ControlRecordStorePort, access: ResourceAccessPort, ledger: LedgerPort
    ) -> None:
        self._raw = raw
        self._access = access
        self._ledger = ledger

    def _require(
        self,
        record: ControlRecord,
        ancestors: frozenset[str] = frozenset(),
        *,
        admitted_draft: bool = False,
        admitting: bool = False,
    ) -> None:
        if record.namespace != "MEMORY":
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        if record.record_type == "POLICY":
            return
        if record.record_digest in ancestors or len(ancestors) >= 128:
            raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
        path = ancestors | {record.record_digest}
        payload = record.payload
        sources = self._strings(payload.get("evidence_refs", ()))
        parent_refs = self._strings(payload.get("parent_refs", ()))
        bound = bool(sources)
        if sources:
            self._access.require_reads(record.project_id, sources)
        inherited_inputs: tuple[str, ...] = ()
        inherited_owner: object = None
        if admitting and record.supersedes_digest is not None:
            prior = self._raw.read_digest(record.project_id, record.supersedes_digest)
            if prior is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            self._require(prior, path)
            if prior.payload.get("resource_lineage_admitted") is True:
                inherited_inputs = self._strings(prior.payload.get("parent_refs", ()))
                inherited_owner = prior.payload.get("domain_revision_ref")
        owner = payload.get("domain_revision_ref")
        if isinstance(owner, str) and owner:
            if owner == inherited_owner or (
                not admitting
                and (admitted_draft or (bound and payload.get("resource_lineage_admitted") is True))
            ):
                self._access.require_admitted_revision(record.project_id, owner)
            else:
                self._access.require_revision(record.project_id, owner)
            bound = True
        inherit_draft = not admitting and (
            admitted_draft or (bound and payload.get("resource_lineage_admitted") is True)
        )
        for ref in parent_refs:
            parent = self._raw.read(record.project_id, "MEMORY", ref) or self._raw.read_digest(
                record.project_id, ref
            )
            if parent is not None:
                self._require(parent, path, admitted_draft=inherit_draft or ref in inherited_inputs)
            else:
                if self._ledger.read_revision_by_digest(record.project_id, ref) is not None:
                    if inherit_draft or ref in inherited_inputs:
                        self._access.require_admitted_revision(record.project_id, ref)
                    else:
                        self._access.require_revision(record.project_id, ref)
                else:
                    self._access.require_read(record.project_id, ref)
            bound = True
        identifiers = list(self._strings(payload.get("conflict_refs", ())))
        for key in ("subject_id", "candidate_id", "memory_entry_id"):
            ref = payload.get(key)
            if isinstance(ref, str) and ref:
                identifiers.append(ref)
        digests = list(self._strings(payload.get("canonical_source_set", ())))
        for key in ("memory_candidate_digest", "memory_entry_digest"):
            ref = payload.get(key)
            if isinstance(ref, str) and ref:
                digests.append(ref)
        if record.supersedes_digest is not None:
            digests.append(record.supersedes_digest)
        for selection in ("included", "excluded", "candidates"):
            entries = payload.get(selection, ())
            if not isinstance(entries, tuple | list):
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            for entry in cast(tuple[object, ...] | list[object], entries):
                if not isinstance(entry, dict):
                    raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
                selection_record = cast(dict[object, object], entry)
                digest = selection_record.get("revision_digest")
                identifier = selection_record.get("memory_entry_id")
                if isinstance(digest, str) and digest:
                    digests.append(digest)
                elif isinstance(identifier, str) and identifier:
                    identifiers.append(identifier)
                else:
                    raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        parents = [self._raw.read(record.project_id, "MEMORY", ref) for ref in identifiers]
        parents.extend(self._raw.read_digest(record.project_id, ref) for ref in digests)
        for parent in parents:
            if parent is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            self._require(parent, path, admitted_draft=inherit_draft)
            bound = True
        if not bound:
            if admitted_draft and payload.get("resource_lineage_admitted") is True:
                return
            actor = current_authenticated_actor()
            creator = "local:operator" if actor is None else actor.actor_id
            if payload.get("resource_origin_actor") != creator:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        items = cast(tuple[object, ...] | list[object], value)
        if any(not isinstance(item, str) for item in items):
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        return tuple(str(item) for item in items)

    def _allowed(self, record: ControlRecord) -> bool:
        try:
            self._require(record)
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                return False
            raise
        return True

    def append(self, value: ControlRecord) -> None:
        self._require(value, admitting=True)
        self._raw.append(value)

    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None:
        item = self._raw.read(project_id, namespace, record_id)
        if item is not None:
            self._require(item)
        return item

    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None:
        item = self._raw.read_digest(project_id, digest)
        if item is not None:
            self._require(item)
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
            if self._allowed(item)
        )


class MemoryControlWriter(ControlRecordService):
    def stage(
        self,
        *,
        project_id: str,
        namespace: str,
        record_type: str,
        state: str,
        payload: dict[str, object],
        record_id: str | None = None,
    ) -> ControlRecord:
        actor = current_authenticated_actor()
        creator = "local:operator" if actor is None else actor.actor_id
        return super().stage(
            project_id=project_id,
            namespace=namespace,
            record_type=record_type,
            state=state,
            record_id=record_id,
            payload={
                **payload,
                "resource_origin_actor": payload.get("resource_origin_actor", creator),
                "resource_revision_actor": creator,
                "resource_lineage_admitted": True,
            },
        )
