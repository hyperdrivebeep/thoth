from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from thoth.application.services.resource_scope_context import resource_stage_scope
from thoth.domain.artifact import ArtifactEnvelope, ParserSelection, StructuralDocument
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    StructuralNodeKind,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ingestion import IngestionResult
from thoth.domain.resource_scope import ResourceIntakeBasis
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.parser import AsyncParserRegistryPort, ParserRegistryPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceScopeAdmissionPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_EVIDENCE_NODE_KINDS = frozenset(
    {
        StructuralNodeKind.SECTION,
        StructuralNodeKind.PARAGRAPH,
        StructuralNodeKind.CELL,
        StructuralNodeKind.ROW,
        StructuralNodeKind.CAPTION,
        StructuralNodeKind.HEADER,
        StructuralNodeKind.UNIT,
        StructuralNodeKind.FOOTNOTE,
        StructuralNodeKind.MENTION,
    }
)


@dataclass(frozen=True)
class IngestArtifactCommand:
    project_id: str
    source_uri: str
    media_type: str
    raw: bytes
    authority: AuthorityState
    cutoff_state: CutoffState
    security_class: SecurityClass
    operation_id: str
    version_label: str | None = None
    source_path: Path | None = None
    parser_selection: ParserSelection | None = None
    classify_source_time: bool = False
    captured_cutoff_at: datetime | None = None
    captured_project_revision: int | None = None


class IngestionService:
    def __init__(
        self,
        *,
        projects: ProjectStorePort,
        objects: ObjectStorePort,
        parsers: ParserRegistryPort,
        artifacts: ArtifactLedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        scopes: ResourceScopeAdmissionPort | None = None,
        source_time_assessor: Callable[..., StructuralDocument] | None = None,
    ) -> None:
        self._projects = projects
        self._objects = objects
        self._parsers = parsers
        self._artifacts = artifacts
        self._clock = clock
        self._ids = ids
        self._scopes = scopes
        self._source_time_assessor = source_time_assessor

    def prepare_resource_scope(self, project_id: str) -> ResourceIntakeBasis | None:
        return None if self._scopes is None else self._scopes.prepare_intake(project_id)

    def ingest(
        self, command: IngestArtifactCommand, *, provenance_only: bool = False
    ) -> IngestionResult:
        result = self.stage(command, provenance_only=provenance_only)
        with resource_stage_scope(result.resource_scope):
            self._artifacts.persist_ingestion(
                result.document, result.source_version_id, result.evidence_candidates
            )
        return result

    def stage(
        self,
        command: IngestArtifactCommand,
        *,
        derived_parents: tuple[str, ...] | None = None,
        provenance_only: bool = False,
    ) -> IngestionResult:
        scope_basis, artifact = self._prepare(command, derived_parents)
        document = (
            self._provenance_document(artifact)
            if provenance_only
            else self._parsers.parse(
                artifact,
                command.raw,
                source_path=command.source_path,
                selection=command.parser_selection,
            )
        )
        return self._finish(command, artifact, scope_basis, document, derived_parents is not None)

    async def ingest_async(self, command: IngestArtifactCommand) -> IngestionResult:
        result = await self.stage_async(command)
        with resource_stage_scope(result.resource_scope):
            self._artifacts.persist_ingestion(
                result.document, result.source_version_id, result.evidence_candidates
            )
        return result

    async def stage_async(
        self, command: IngestArtifactCommand, *, derived_parents: tuple[str, ...] | None = None
    ) -> IngestionResult:
        scope_basis, artifact = self._prepare(command, derived_parents)
        if isinstance(self._parsers, AsyncParserRegistryPort):
            document = await self._parsers.parse_async(
                artifact,
                command.raw,
                source_path=command.source_path,
                selection=command.parser_selection,
            )
        else:
            document = self._parsers.parse(
                artifact,
                command.raw,
                source_path=command.source_path,
                selection=command.parser_selection,
            )
        return self._finish(command, artifact, scope_basis, document, derived_parents is not None)

    @staticmethod
    def _provenance_document(artifact: ArtifactEnvelope) -> StructuralDocument:
        return StructuralDocument(
            artifact=artifact.model_copy(update={"parser_name": "provenance-only"}),
            nodes=(),
            extraction_coverage="NONE",
        )

    def _prepare(
        self, command: IngestArtifactCommand, derived_parents: tuple[str, ...] | None
    ) -> tuple[ResourceIntakeBasis | None, ArtifactEnvelope]:
        scope_basis = (
            self.prepare_resource_scope(command.project_id)
            if derived_parents is None or self._scopes is None
            else self._scopes.prepare_derived_intake(command.project_id, derived_parents)
        )
        project = self._projects.read(command.project_id)
        if project is None:
            raise ValueError("project does not exist")
        if (
            command.captured_project_revision is not None
            and project.revision != command.captured_project_revision
        ):
            raise ValueError("project revision changed before source ingestion")
        if (
            command.captured_cutoff_at is not None
            and project.cutoff_at != command.captured_cutoff_at
        ):
            raise ValueError("project cutoff changed before source ingestion")
        pending_cutoff = (
            CutoffState.UNKNOWN_TIME
            if command.classify_source_time
            and command.cutoff_state != CutoffState.PROHIBITED_CONTEXT
            else command.cutoff_state
        )
        byte_digest = hashlib.sha256(command.raw).hexdigest()
        artifact = ArtifactEnvelope(
            artifact_id=self._ids.new("artifact"),
            project_id=command.project_id,
            source_uri=command.source_uri,
            media_type=command.media_type,
            byte_sha256=byte_digest,
            version_label=command.version_label,
            authority=command.authority,
            cutoff_state=pending_cutoff,
            security_class=command.security_class,
            retrieved_at=self._clock.now(),
            parser_name="unparsed",
            parser_version="0",
        )
        return scope_basis, artifact

    def _finish(
        self,
        command: IngestArtifactCommand,
        artifact: ArtifactEnvelope,
        scope_basis: ResourceIntakeBasis | None,
        document: StructuralDocument,
        derived: bool,
    ) -> IngestionResult:
        self._objects.put(command.raw, artifact.byte_sha256, operation_id=command.operation_id)
        source_version_id = self._ids.new("source-version")
        if document.capability_observation is not None:
            document = document.model_copy(
                update={
                    "capability_observation": document.capability_observation.model_copy(
                        update={"source_version_id": source_version_id}
                    )
                }
            )
        project = self._projects.read(command.project_id)
        if project is None:
            raise ValueError("project does not exist")
        if (
            command.captured_project_revision is not None
            and project.revision != command.captured_project_revision
        ):
            raise ValueError("project revision changed before source ingestion")
        if (
            command.captured_cutoff_at is not None
            and project.cutoff_at != command.captured_cutoff_at
        ):
            raise ValueError("project cutoff changed before source ingestion")
        if command.classify_source_time:
            if self._source_time_assessor is None:
                raise ValueError("source time assessor is required for classified ingestion")
            document = self._source_time_assessor(
                document,
                source_version_id=source_version_id,
                cutoff_at=command.captured_cutoff_at or project.cutoff_at,
                project_revision=project.revision,
                classify=True,
                current_cutoff_state=artifact.cutoff_state,
            )
        assessed = document.artifact
        evidence = tuple(
            EvidenceSpan(
                span_id=self._ids.new("span"),
                project_id=command.project_id,
                artifact_id=assessed.artifact_id,
                source_version_id=source_version_id,
                locator=node.locator.model_copy(update={"structural_node_id": node.node_id}),
                exact_text=node.text,
                text_sha256=hashlib.sha256(node.text.encode()).hexdigest(),
                extraction_method=f"{document.artifact.parser_name}:{document.artifact.parser_version}",
                support_state=SupportState.EXTRACTED,
                authority_state=assessed.authority,
                verification_state=VerificationState.SCHEMA_VALID,
                cutoff_state=assessed.cutoff_state,
            )
            for node in document.nodes
            if node.kind in _EVIDENCE_NODE_KINDS and node.text
        )
        return IngestionResult(
            document=document,
            source_version_id=source_version_id,
            evidence_candidates=evidence,
            object_digest=assessed.byte_sha256,
            resource_scope=None
            if self._scopes is None or scope_basis is None
            else self._scopes.stage(assessed.artifact_id, scope_basis, derived=derived),
        )
