from __future__ import annotations

from typing import cast

from pydantic import AwareDatetime, Field, JsonValue

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.export_snapshot import ExportSnapshotResolver
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.closure import Export
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import SecurityClass
from thoth.domain.export_snapshot import FrozenExportSnapshot, StagedExportBundle
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.export_bundle import ExportBundlePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import AtomicUnitOfWorkPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

PURPOSES = {
    "AUDIT_BUNDLE",
    "REVIEW_HANDOFF",
    "REPRODUCIBILITY_PACKAGE",
    "REGULATORY_SUBMISSION",
    "COLLABORATOR_HANDOFF",
    "PUBLICATION_PACKAGE",
    "INTERNAL_BACKUP",
}


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ExportReadInput(ProjectInput):
    export_id: str = Field(min_length=1, max_length=160)


class PlanCreateInput(ProjectInput):
    purpose: str = Field(min_length=1, max_length=80)
    recipient: str = Field(min_length=1, max_length=500)
    trust_boundary: str = Field(pattern=r"^(LOCAL_ONLY|INTERNAL_AUTHORIZED|EXTERNAL_PROTECTED)$")
    scope_refs: tuple[str, ...]
    cutoff: AwareDatetime
    head_set: dict[str, str]
    baseline_set_digest: str | None = Field(default=None, min_length=64, max_length=64)
    selection_rules: dict[str, JsonValue]
    classification_ceiling: SecurityClass
    rights_policy_ref: str = Field(min_length=1, max_length=160)
    privacy_policy_ref: str = Field(min_length=1, max_length=160)
    renderers: tuple[str, ...] = ()


class SnapshotCreateInput(ProjectInput):
    export_plan_id: str = Field(min_length=1, max_length=160)
    expected_plan_revision: int = Field(ge=1)


class GenerateInput(ProjectInput):
    export_snapshot_id: str = Field(min_length=1, max_length=160)


class VerifyInput(ExportReadInput):
    verification_policy_ref: str = Field(default="export-verify:1", max_length=160)


class ReleasePrepareInput(ExportReadInput):
    target: str = Field(min_length=1, max_length=500)
    release_boundary: str = Field(pattern=r"^(LOCAL_ONLY|INTERNAL_AUTHORIZED|EXTERNAL_PROTECTED)$")
    actor_ref: str = Field(min_length=1, max_length=160)


class CorrectionCreateInput(ExportReadInput):
    correction_reason: str = Field(min_length=1, max_length=2_000)
    corrections: dict[str, JsonValue]


class ExportHandlers:
    def __init__(
        self,
        *,
        bundles: ExportBundlePort,
        resolver: ExportSnapshotResolver,
        unit_of_work: AtomicUnitOfWorkPort,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        ledger: LedgerPort,
        baselines: BaselineService,
    ) -> None:
        self._bundles = bundles
        self._resolver = resolver
        self._unit_of_work = unit_of_work
        self._records = records
        self._controls = controls
        self._ledger = ledger
        self._baselines = baselines

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        controls = self._records.list(request.project_id, "EXPORT", None)
        legacy: list[JsonValue] = []
        for key, digest in self._ledger.read_heads(request.project_id).items():
            if not key.startswith("EXPORT:"):
                continue
            try:
                revision = self._ledger.read_revision_by_digest(request.project_id, digest)
                snapshot = (
                    None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
                )
            except ResourceScopeError:
                continue
            if snapshot is not None:
                item = Export.model_validate(snapshot.content)
                visible_receipts = {
                    receipt.receipt_id for receipt in self._ledger.read_receipts(request.project_id)
                }
                if not set(item.receipt_refs).issubset(visible_receipts):
                    continue
                try:
                    self._resolver.require_artifact_access(request.project_id, item.artifact_refs)
                except ResourceScopeError:
                    continue
                legacy.append(item.model_dump(mode="json"))
        return cast(
            dict[str, JsonValue],
            {
                "exports": [
                    item.model_dump(mode="json") for item in controls if self._visible(item)
                ]
                + legacy
            },
        )

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        return {"export": self._record(request).model_dump(mode="json")}

    async def plan_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        return {"plan": self._record(request, "PLAN").model_dump(mode="json")}

    async def snapshot_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        return {"snapshot": self._record(request, "SNAPSHOT").model_dump(mode="json")}

    async def manifest_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        item = self._record(request, "GENERATED_EXPORT")
        return {"manifest": cast(JsonValue, item.payload.get("manifest"))}

    async def artifact_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        item = self._record(request, "GENERATED_EXPORT")
        return {"artifacts": cast(JsonValue, item.payload.get("artifacts", ()))}

    async def verification_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "EXPORT", "VERIFICATION")
            if item.payload.get("export_id") == request.export_id
        )
        return {
            "verifications": [item.model_dump(mode="json") for item in items if self._visible(item)]
        }

    async def release_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "EXPORT", "RELEASE")
            if item.payload.get("export_id") == request.export_id
        )
        return {"releases": [item.model_dump(mode="json") for item in items if self._visible(item)]}

    async def correction_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "EXPORT", "CORRECTION")
            if item.record_id == request.export_id
            or item.payload.get("corrected_export_id") == request.export_id
        )
        return {
            "corrections": [item.model_dump(mode="json") for item in items if self._visible(item)]
        }

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ExportReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "EXPORT", None, latest_only=False)
            if item.record_id == request.export_id
            or item.payload.get("export_id") == request.export_id
            or item.payload.get("plan_id") == request.export_id
            or item.payload.get("snapshot_id") == request.export_id
        )
        return {"records": [item.model_dump(mode="json") for item in items if self._visible(item)]}

    async def plan_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = PlanCreateInput.model_validate(value)
            if request.purpose not in PURPOSES:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "unknown export purpose")
            current_heads = dict(self._ledger.read_heads(request.project_id))
            if request.head_set != current_heads:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "Export plan HeadSet is not current"
                )
            if request.baseline_set_digest is not None and self._baselines.list_sets(
                request.project_id
            ):
                try:
                    self._baselines.require_current_comparator(
                        request.project_id,
                        request.baseline_set_digest,
                    )
                except ValueError as exc:
                    raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            plan = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="PLAN",
                state="PLANNED",
                payload={
                    "purpose": request.purpose,
                    "recipient": request.recipient,
                    "trust_boundary": request.trust_boundary,
                    "scope_refs": request.scope_refs,
                    "cutoff": request.cutoff,
                    "head_set": request.head_set,
                    "head_set_digest": head_set_digest(request.head_set),
                    "baseline_set_digest": request.baseline_set_digest,
                    "selection_rules": cast(dict[str, object], request.selection_rules),
                    "classification_ceiling": request.classification_ceiling,
                    "rights_policy_ref": request.rights_policy_ref,
                    "privacy_policy_ref": request.privacy_policy_ref,
                    "renderers": request.renderers,
                    "release_boundary": request.trust_boundary,
                },
            )
            self._require_resource_access(plan, set())
            return {"export_plan": plan.model_dump(mode="json")}

    async def snapshot_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SnapshotCreateInput.model_validate(value)
        with self._unit_of_work.transaction():
            plan = self._records.read(request.project_id, "EXPORT", request.export_plan_id)
            if plan is None or plan.record_type != "PLAN":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_PLAN_NOT_FOUND")
            if plan.version != request.expected_plan_revision:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_PLAN_CHANGED")
            if plan.payload.get("head_set") != dict(self._ledger.read_heads(request.project_id)):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_HEAD_CHANGED")
            try:
                frozen = self._resolver.freeze(plan)
            except ValueError as exc:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
            snapshot = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="SNAPSHOT",
                state="SNAPSHOT_SEALED",
                payload={
                    **frozen.scope.model_dump(mode="python"),
                    "plan_id": plan.record_id,
                    "plan_version": plan.version,
                    "plan_digest": plan.record_digest,
                    "frozen_snapshot": frozen.model_dump(mode="python"),
                },
            )
        return {"export_snapshot": snapshot.model_dump(mode="json")}

    async def generate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = GenerateInput.model_validate(value)
        with self._unit_of_work.transaction():
            snapshot, frozen = self._snapshot(request.project_id, request.export_snapshot_id)
            self._require_basis(frozen)
        # File I/O is outside the database transaction, in a fresh exclusive directory.
        bundle = self._bundles.stage(frozen, snapshot.record_digest)
        valid, _ = self._bundles.verify(bundle)
        if not valid:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_STAGING_INVALID")
        with self._unit_of_work.transaction():
            current, _ = self._snapshot(request.project_id, request.export_snapshot_id)
            if current.record_digest != snapshot.record_digest:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_SNAPSHOT_CHANGED")
            self._require_basis(frozen)
            record = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="GENERATED_EXPORT",
                state="GENERATED",
                payload={
                    "snapshot_id": snapshot.record_id,
                    "snapshot_digest": snapshot.record_digest,
                    "plan_id": frozen.plan_id,
                    "plan_version": frozen.plan_version,
                    "plan_digest": frozen.plan_digest,
                    "recipient": frozen.scope.recipient,
                    "trust_boundary": frozen.scope.trust_boundary,
                    **bundle.model_dump(mode="python"),
                    "resources": tuple(
                        item.model_dump(mode="python")
                        for item in frozen.resources
                        if item.inclusion_mode == "FULL"
                    ),
                    "excluded_resources": tuple(
                        item.model_dump(mode="python")
                        for item in frozen.resources
                        if item.inclusion_mode == "EXCLUDED"
                    ),
                    "release_eligible": all(
                        item.inclusion_mode == "FULL" for item in frozen.resources
                    ),
                    "renderer_outputs": (),
                    "external_transmission_performed": False,
                },
            )
        return {"export": record.model_dump(mode="json")}

    async def verify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = VerifyInput.model_validate(value)
        export = self._record(request, "GENERATED_EXPORT")
        valid, failed = self._bundles.verify(self._bundle(export))
        with self._unit_of_work.transaction():
            self._require_export_current(export)
            legacy_unbound = "snapshot_digest" not in export.payload
            frozen = None if legacy_unbound else self._export_basis(export)
            rights_clear = frozen is not None and all(
                item.inclusion_mode == "FULL" for item in frozen.resources
            )
            verification = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="VERIFICATION",
                state="VERIFIED" if valid else "REJECTED",
                payload={
                    "export_id": export.record_id,
                    "export_digest": export.record_digest,
                    "verification_policy_ref": request.verification_policy_ref,
                    "file_integrity": valid,
                    "snapshot_binding": "LEGACY_UNBOUND" if legacy_unbound else "FROZEN",
                    "failed_files": failed,
                    "rights_clear_for_bound_scope": rights_clear,
                    "release_eligible": valid and rights_clear,
                },
            )
        return {"verification": verification.model_dump(mode="json")}

    async def release_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReleasePrepareInput.model_validate(value)
        export = self._record(request, "GENERATED_EXPORT")
        valid, _ = self._bundles.verify(self._bundle(export))
        if not valid:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_FILES_CHANGED")
        with self._unit_of_work.transaction():
            self._require_export_current(export)
            frozen = self._export_basis(export)
            if (
                request.target != frozen.scope.recipient
                or request.release_boundary != frozen.scope.trust_boundary
            ):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "EXPORT_RELEASE_SCOPE_CHANGED"
                )
            verification = next(
                (
                    item
                    for item in self._records.list(request.project_id, "EXPORT", "VERIFICATION")
                    if item.payload.get("export_id") == export.record_id
                    and item.payload.get("export_digest") == export.record_digest
                    and item.state == "VERIFIED"
                    and item.payload.get("release_eligible") is True
                ),
                None,
            )
            if verification is None:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "EXPORT_VERIFICATION_REQUIRED"
                )
            release = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="RELEASE",
                state="RELEASE_PREPARED",
                payload={
                    "export_id": export.record_id,
                    "export_digest": export.record_digest,
                    "snapshot_digest": export.payload.get("snapshot_digest"),
                    "target": request.target,
                    "release_boundary": request.release_boundary,
                    "actor_ref": request.actor_ref,
                    "risk_tier": "R3" if request.release_boundary == "EXTERNAL_PROTECTED" else "R1",
                    "exact_scope_digest": domain_digest(
                        "EXPORT_RELEASE_SCOPE",
                        "1.0.0",
                        canonical_payload(
                            {
                                "export_digest": export.record_digest,
                                "target": request.target,
                                "boundary": request.release_boundary,
                            }
                        ),
                    ),
                    "transmitted": False,
                    "submitted": False,
                    "published": False,
                },
            )
        return {"release": release.model_dump(mode="json")}

    async def correction_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = CorrectionCreateInput.model_validate(value)
            original = self._record(request, "GENERATED_EXPORT")
            if set(request.corrections) - {"manifest_note"} or any(
                not isinstance(item, str) or len(item) > 2000
                for item in request.corrections.values()
            ):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "EXPORT_CORRECTION_SCOPE_CHANGED"
                )
            self._export_basis(original)
            correction = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="CORRECTION",
                state="CORRECTED",
                payload={
                    "original_export_id": original.record_id,
                    "original_export_digest": original.record_digest,
                    "correction_reason": request.correction_reason,
                    "corrections": cast(dict[str, object], request.corrections),
                    "original_overwritten": False,
                },
            )
            corrected = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="GENERATED_EXPORT",
                state="CORRECTED",
                payload={
                    **original.payload,
                    "supersedes_export_id": original.record_id,
                    "correction_id": correction.record_id,
                    "corrections": cast(dict[str, object], request.corrections),
                    "external_transmission_performed": False,
                },
            )
            correction = self._controls.create(
                project_id=request.project_id,
                namespace="EXPORT",
                record_type="CORRECTION",
                record_id=correction.record_id,
                state="CORRECTED",
                payload={
                    **correction.payload,
                    "corrected_export_id": corrected.record_id,
                    "corrected_export_digest": corrected.record_digest,
                },
            )
            return {
                "correction": correction.model_dump(mode="json"),
                "corrected_export": corrected.model_dump(mode="json"),
                "original_overwritten": False,
            }

    def _record(self, request: ExportReadInput, record_type: str | None = None):
        item = self._records.read(request.project_id, "EXPORT", request.export_id)
        if item is None or (record_type is not None and item.record_type != record_type):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "export record not found")
        self._require_resource_access(item, set())
        return item

    def _visible(self, record: ControlRecord) -> bool:
        try:
            self._require_resource_access(record, set())
            return True
        except ResourceScopeError:
            return False

    def _require_resource_access(self, record: ControlRecord, ancestors: set[str]) -> None:
        if record.record_id in ancestors or len(ancestors) >= 128:
            raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
        ancestors.add(record.record_id)
        try:
            if record.record_type == "PLAN":
                try:
                    self._resolver.freeze(record)
                except ResourceScopeError:
                    raise
                except ValueError as exc:
                    raise ResourceScopeError("RESOURCE_REFERENCE_UNRESOLVED") from exc
                return
            if record.record_type == "SNAPSHOT":
                try:
                    frozen = FrozenExportSnapshot.model_validate(
                        record.payload.get("frozen_snapshot")
                    )
                except ValueError as exc:
                    raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN") from exc
                if frozen.project_id != record.project_id:
                    raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
                self._resolver.require_access(frozen)
                return
            references = tuple(
                dict.fromkeys(
                    str(record.payload[key])
                    for key in (
                        "snapshot_id",
                        "plan_id",
                        "export_id",
                        "original_export_id",
                        "corrected_export_id",
                    )
                    if isinstance(record.payload.get(key), str)
                )
            )
            if not references:
                raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN")
            for ref in references:
                parent = self._records.read(record.project_id, "EXPORT", ref)
                if parent is None:
                    raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN")
                self._require_resource_access(parent, ancestors)
        finally:
            ancestors.remove(record.record_id)

    def _snapshot(
        self, project_id: str, snapshot_id: str
    ) -> tuple[ControlRecord, FrozenExportSnapshot]:
        record = self._records.read(project_id, "EXPORT", snapshot_id)
        if record is None or record.record_type != "SNAPSHOT":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_SNAPSHOT_NOT_FOUND")
        if "frozen_snapshot" not in record.payload:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "EXPORT_LEGACY_SNAPSHOT_RESEAL_REQUIRED"
            )
        try:
            frozen = FrozenExportSnapshot.model_validate(record.payload["frozen_snapshot"])
        except ValueError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "EXPORT_SNAPSHOT_INVALID"
            ) from exc
        if frozen.project_id != project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_PROJECT_SCOPE_MISMATCH")
        return record, frozen

    def _require_basis(self, frozen: FrozenExportSnapshot) -> None:
        plan = self._records.read(frozen.project_id, "EXPORT", frozen.plan_id)
        if plan is None or plan.record_type != "PLAN":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_PLAN_NOT_FOUND")
        try:
            self._resolver.require_unchanged(plan, frozen)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    def _export_basis(self, export: ControlRecord) -> FrozenExportSnapshot:
        if "snapshot_digest" not in export.payload:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "EXPORT_LEGACY_SNAPSHOT_RESEAL_REQUIRED"
            )
        snapshot, frozen = self._snapshot(export.project_id, str(export.payload.get("snapshot_id")))
        if snapshot.record_digest != export.payload.get("snapshot_digest"):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_SNAPSHOT_CHANGED")
        self._require_basis(frozen)
        return frozen

    def _require_export_current(self, export: ControlRecord) -> None:
        current = self._records.read(export.project_id, "EXPORT", export.record_id)
        if current is None or current.record_digest != export.record_digest:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "EXPORT_REVISION_CHANGED")

    @staticmethod
    def _bundle(export: ControlRecord) -> StagedExportBundle:
        return StagedExportBundle.model_validate(
            {key: export.payload.get(key) for key in ("root", "artifacts", "manifest")}
        )
