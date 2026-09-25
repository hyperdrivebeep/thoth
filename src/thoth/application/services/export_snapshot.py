from __future__ import annotations

from collections import deque

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.export_snapshot import (
    ExportPlanScope,
    ExportProvenanceBinding,
    FrozenExportResource,
    FrozenExportSnapshot,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.ledger import LedgerPort

_CLASSIFICATION = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}


class ExportSnapshotResolver:
    """Resolve explicit stored revisions and their declared evidence; never expand a project."""

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        artifacts: ArtifactLedgerPort,
        evidence: EvidenceGraphStorePort,
    ) -> None:
        self._ledger = ledger
        self._artifacts = artifacts
        self._evidence = evidence

    def freeze(self, plan: ControlRecord) -> FrozenExportSnapshot:
        scope = ExportPlanScope.model_validate(plan.payload)
        # Selection is bounded by the explicit scope. This is the only current filter language.
        if scope.selection_rules not in ({}, {"include": "rights-cleared public"}):
            raise ValueError("EXPORT_SELECTION_RULE_UNSUPPORTED")
        if scope.renderers and set(scope.renderers) - {"JSON"}:
            raise ValueError("EXPORT_RENDERER_UNSUPPORTED")
        revisions: dict[str, str] = {}
        bindings: dict[str, ExportProvenanceBinding] = {}
        artifact_ids: set[str] = set()
        selected_sources: dict[str, set[str]] = {}
        queue: deque[str] = deque()
        for ref in scope.scope_refs:
            digest = scope.head_set.get(ref)
            if digest is None:
                queue.append(ref)
                continue
            revision = self._ledger.read_revision_by_digest(plan.project_id, digest)
            if revision is None or (
                revision.project_id != plan.project_id
                or f"{revision.entity_type.value}:{revision.entity_id}" != ref
                or revision.revision_digest != digest
            ):
                raise ValueError("EXPORT_REVISION_UNRESOLVED")
            snapshot = self._ledger.read_snapshot(revision.snapshot_id)
            if snapshot is None or (
                snapshot.project_id != plan.project_id
                or snapshot.entity_id != revision.entity_id
                or snapshot.entity_type != revision.entity_type
            ):
                raise ValueError("EXPORT_REVISION_SNAPSHOT_UNRESOLVED")
            # A revision can have no evidence. That resolves to no artifact, never all artifacts.
            revisions[ref] = digest
            bindings[ref] = self._binding(ref, "REVISION", revision.model_dump(mode="python"))
            bindings[snapshot.snapshot_id] = self._binding(
                snapshot.snapshot_id, "ENTITY_SNAPSHOT", snapshot.model_dump(mode="python")
            )
            queue.extend(revision.evidence_refs)
        visited: set[str] = set()
        while queue:
            ref = queue.popleft()
            if ref in visited:
                continue
            visited.add(ref)
            if len(visited) + len(bindings) > 10000:
                raise ValueError("EXPORT_SCOPE_LIMIT")
            kind, payload, children, artifact_id = self._resolve_ref(plan.project_id, ref)
            bindings[ref] = self._binding(ref, kind, payload)
            queue.extend(children)
            if artifact_id is not None:
                artifact_ids.add(artifact_id)
                if kind == "SOURCE":
                    selected_sources.setdefault(artifact_id, set()).add(ref)
        resources = tuple(
            self._resource(
                plan.project_id,
                artifact_id,
                scope,
                tuple(sorted(selected_sources.get(artifact_id, set()))),
            )
            for artifact_id in sorted(artifact_ids)
        )
        return FrozenExportSnapshot(
            project_id=plan.project_id,
            plan_id=plan.record_id,
            plan_version=plan.version,
            plan_digest=plan.record_digest,
            scope=scope,
            selected_revisions=revisions,
            provenance_bindings=tuple(bindings[key] for key in sorted(bindings)),
            resources=resources,
        )

    def require_unchanged(self, plan: ControlRecord, frozen: FrozenExportSnapshot) -> None:
        if (
            plan.record_id != frozen.plan_id
            or plan.version != frozen.plan_version
            or plan.record_digest != frozen.plan_digest
        ):
            raise ValueError("EXPORT_PLAN_CHANGED")
        current = self.freeze(plan)
        if canonical_payload(current.model_dump(mode="python")) != canonical_payload(
            frozen.model_dump(mode="python")
        ):
            raise ValueError("EXPORT_RESOURCE_BASIS_CHANGED")

    def require_access(self, frozen: FrozenExportSnapshot) -> None:
        self.require_artifact_access(
            frozen.project_id, tuple(item.artifact.artifact_id for item in frozen.resources)
        )

    def require_artifact_access(self, project_id: str, artifact_refs: tuple[str, ...]) -> None:
        for reference in artifact_refs:
            artifact = self._artifacts.read_artifact(reference)
            if artifact is None or artifact.project_id != project_id:
                raise ValueError("EXPORT_ARTIFACT_UNRESOLVED")

    def _resolve_ref(
        self, project_id: str, ref: str
    ) -> tuple[str, dict[str, object], tuple[str, ...], str | None]:
        artifact = self._artifacts.read_artifact(ref)
        if artifact is not None:
            self._require_project(project_id, artifact.project_id)
            return "ARTIFACT", artifact.model_dump(mode="python"), (), artifact.artifact_id
        span = self._artifacts.read_evidence(ref)
        if span is not None:
            self._require_project(project_id, span.project_id)
            if span.cutoff_state.value != "ELIGIBLE":
                raise ValueError("EXPORT_SPAN_CUTOFF_INELIGIBLE")
            return "SPAN", span.model_dump(mode="python"), (), span.artifact_id
        source = self._evidence.read_source(ref)
        if source is not None:
            self._require_project(project_id, source.project_id)
            return "SOURCE", source.model_dump(mode="python"), (), source.artifact_id
        observation = self._evidence.read_observation(ref)
        if observation is not None:
            self._require_project(project_id, observation.project_id)
            return "OBSERVATION", observation.model_dump(mode="python"), observation.span_ids, None
        link = self._evidence.read_link(ref)
        if link is not None:
            self._require_project(project_id, link.project_id)
            if link.cutoff_eligibility.value != "ELIGIBLE":
                raise ValueError("EXPORT_LINK_CUTOFF_INELIGIBLE")
            return (
                "EVIDENCE_LINK",
                link.model_dump(mode="python"),
                (*link.source_ids, *link.span_ids, *link.observation_ids),
                None,
            )
        raise ValueError("EXPORT_SCOPE_UNRESOLVED")

    def _resource(
        self,
        project_id: str,
        artifact_id: str,
        scope: ExportPlanScope,
        selected_source_refs: tuple[str, ...],
    ) -> FrozenExportResource:
        artifact = self._artifacts.read_artifact(artifact_id)
        if artifact is None:
            raise ValueError("EXPORT_ARTIFACT_UNRESOLVED")
        self._require_project(project_id, artifact.project_id)
        current_source = self._evidence.read_source_by_artifact(artifact_id)
        sources = {
            item.source_id: item
            for ref in selected_source_refs
            if (item := self._evidence.read_source(ref)) is not None
        }
        if len(sources) != len(selected_source_refs):
            raise ValueError("EXPORT_SOURCE_UNRESOLVED")
        if current_source is not None:
            sources[current_source.source_id] = current_source
        source_bindings = tuple(sources[key] for key in sorted(sources))
        reasons: list[str] = []
        classes = [artifact.security_class.value]
        rights = [item.rights for item in source_bindings] or ["UNKNOWN"]
        for source in source_bindings:
            self._require_project(project_id, source.project_id)
            if source.artifact_id != artifact_id or source.sha256 != artifact.byte_sha256:
                raise ValueError("EXPORT_SOURCE_ARTIFACT_MISMATCH")
            classes.append(source.security_class)
            if source.cutoff_eligibility.value != "ELIGIBLE":
                reasons.append("CUTOFF_INELIGIBLE")
            # Retrieval time and evidence valid time are distinct.
            if source.valid_time is not None and source.valid_time > scope.cutoff:
                reasons.append("AFTER_CUTOFF")
        if any(item not in _CLASSIFICATION for item in classes):
            reasons.append("CLASSIFICATION_UNKNOWN")
        elif (
            max(_CLASSIFICATION[item] for item in classes)
            > _CLASSIFICATION[scope.classification_ceiling.value]
        ):
            reasons.append("CLASSIFICATION_CEILING_EXCEEDED")
        if artifact.cutoff_state.value != "ELIGIBLE":
            reasons.append("CUTOFF_INELIGIBLE")
        if "PROHIBITED" in rights:
            reasons.append("RIGHTS_PROHIBITED")
        public_only = (
            scope.trust_boundary == "EXTERNAL_PROTECTED"
            or scope.selection_rules.get("include") == "rights-cleared public"
        )
        if public_only:
            if any(item != "REUSE_ALLOWED" for item in rights):
                reasons.append("EXTERNAL_RIGHTS_UNRESOLVED")
            if any(item != "PUBLIC" for item in classes):
                reasons.append("EXTERNAL_CLASSIFICATION_BLOCKED")
        return FrozenExportResource(
            artifact=artifact,
            source=current_source,
            source_bindings=source_bindings,
            inclusion_mode="EXCLUDED" if reasons else "FULL",
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _binding(ref: str, kind: str, payload: dict[str, object]) -> ExportProvenanceBinding:
        return ExportProvenanceBinding(
            ref=ref,
            kind=kind,
            digest=domain_digest("EXPORT_PROVENANCE", "1.0.0", canonical_payload(payload)),
        )

    @staticmethod
    def _require_project(expected: str, actual: str) -> None:
        if actual != expected:
            raise ValueError("EXPORT_PROJECT_SCOPE_MISMATCH")
