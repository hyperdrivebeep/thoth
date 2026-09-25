from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import RowMapping

from thoth.adapters.storage.schema import (
    artifact_versions,
    artifacts,
    evidence_spans,
    structural_nodes,
)
from thoth.adapters.storage.structure_payload import structure_metadata
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import ProjectId, SourceVersionId
from thoth.domain.source_time import (
    SourceTimeAssessment,
    SourceTimeAssessmentMode,
    SourceTimeError,
    SourceTimeMutationBasis,
    SourceTimeMutationReason,
    SourceTimeReasonCode,
    build_source_time_assessment,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def _json_dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


class SqliteArtifactLedger(ArtifactLedgerPort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def persist_ingestion(
        self,
        document: StructuralDocument,
        source_version_id: SourceVersionId,
        evidence_candidates: tuple[EvidenceSpan, ...],
    ) -> None:
        artifact = document.artifact
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(artifacts).values(
                    artifact_id=artifact.artifact_id,
                    project_id=artifact.project_id,
                    source_uri=artifact.source_uri,
                    media_type=artifact.media_type,
                    byte_sha256=artifact.byte_sha256,
                    authority=artifact.authority.value,
                    cutoff_state=artifact.cutoff_state.value,
                    security_class=artifact.security_class.value,
                    parser_name=artifact.parser_name,
                    parser_version=artifact.parser_version,
                    retrieved_at=artifact.retrieved_at.isoformat(),
                )
            )
            connection.execute(
                insert(artifact_versions).values(
                    source_version_id=source_version_id,
                    artifact_id=artifact.artifact_id,
                    version_label=artifact.version_label,
                    structure_metadata_json=structure_metadata(document),
                    byte_sha256=artifact.byte_sha256,
                    retrieved_at=artifact.retrieved_at.isoformat(),
                )
            )
            for node in document.nodes:
                connection.execute(
                    insert(structural_nodes).values(
                        node_id=node.node_id,
                        project_id=artifact.project_id,
                        artifact_id=artifact.artifact_id,
                        parent_id=node.parent_id,
                        kind=node.kind.value,
                        ordinal=node.ordinal,
                        text=node.text,
                        locator_json=_json_dump(node.locator.model_dump(mode="json")),
                        source_version_id=source_version_id,
                        content_json=node.model_dump_json(),
                    )
                )
                if node.text:
                    connection.exec_driver_sql(
                        "INSERT INTO structural_fts(node_id, project_id, artifact_id, text) "
                        "VALUES (?, ?, ?, ?)",
                        (node.node_id, artifact.project_id, artifact.artifact_id, node.text),
                    )
            for span in evidence_candidates:
                connection.execute(
                    insert(evidence_spans).values(
                        span_id=span.span_id,
                        project_id=span.project_id,
                        artifact_id=span.artifact_id,
                        source_version_id=span.source_version_id,
                        locator_json=_json_dump(span.locator.model_dump(mode="json")),
                        exact_text=span.exact_text,
                        text_sha256=span.text_sha256,
                        extraction_method=span.extraction_method,
                        support_state=span.support_state.value,
                        authority_state=span.authority_state.value,
                        verification_state=span.verification_state.value,
                        cutoff_state=span.cutoff_state.value,
                    )
                )

    def list_source_versions(self, project_id: str, artifact_id: str) -> tuple[str, ...]:
        with read_connection(self._engine) as connection:
            return tuple(
                str(row[0])
                for row in connection.execute(
                    select(artifact_versions.c.source_version_id)
                    .join(artifacts, artifacts.c.artifact_id == artifact_versions.c.artifact_id)
                    .where(
                        artifacts.c.project_id == project_id, artifacts.c.artifact_id == artifact_id
                    )
                    .order_by(
                        artifact_versions.c.retrieved_at, artifact_versions.c.source_version_id
                    )
                )
            )

    def read_structure(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> StructuralDocument | None:
        with read_connection(self._engine) as connection:
            source = (
                connection.execute(
                    select(
                        artifacts,
                        artifact_versions.c.version_label,
                        artifact_versions.c.structure_metadata_json,
                    )
                    .join(
                        artifact_versions,
                        artifact_versions.c.artifact_id == artifacts.c.artifact_id,
                    )
                    .where(
                        artifacts.c.project_id == project_id,
                        artifacts.c.artifact_id == artifact_id,
                        artifact_versions.c.source_version_id == source_version_id,
                    )
                )
                .mappings()
                .first()
            )
            if source is None:
                return None
            predicate = structural_nodes.c.source_version_id == source_version_id
            if source["structure_metadata_json"] is None:
                versions = connection.execute(
                    select(artifact_versions.c.source_version_id).where(
                        artifact_versions.c.artifact_id == artifact_id
                    )
                ).all()
                if len(versions) != 1:
                    return None  # Legacy rows cannot prove which source version owns them.
                predicate = structural_nodes.c.source_version_id.is_(None)
            rows = (
                connection.execute(
                    select(structural_nodes)
                    .where(
                        structural_nodes.c.project_id == project_id,
                        structural_nodes.c.artifact_id == artifact_id,
                        predicate,
                    )
                    .order_by(structural_nodes.c.ordinal, structural_nodes.c.node_id)
                )
                .mappings()
                .all()
            )
            nodes: list[StructuralNode] = []
            for row in rows:
                if row["content_json"] is not None:
                    node = StructuralNode.model_validate_json(str(row["content_json"]))
                    if (
                        node.node_id,
                        node.artifact_id,
                        node.kind.value,
                        node.parent_id,
                        node.ordinal,
                        node.text,
                        node.locator,
                    ) != (
                        row["node_id"],
                        row["artifact_id"],
                        row["kind"],
                        row["parent_id"],
                        row["ordinal"],
                        row["text"],
                        SourceLocator.model_validate_json(str(row["locator_json"])),
                    ):
                        raise ValueError("STRUCTURE_PROJECTION_MISMATCH")
                    nodes.append(node)
                else:
                    nodes.append(
                        StructuralNode.model_validate(
                            {
                                "node_id": row["node_id"],
                                "artifact_id": artifact_id,
                                "kind": row["kind"],
                                "parent_id": row["parent_id"],
                                "ordinal": row["ordinal"],
                                "text": row["text"],
                                "locator": orjson.loads(str(row["locator_json"])),
                            }
                        )
                    )
            metadata = (
                {"extraction_coverage": "UNKNOWN"}
                if source["structure_metadata_json"] is None
                else orjson.loads(str(source["structure_metadata_json"]))
            )
            if source["structure_metadata_json"] is not None and (
                metadata.pop("nodes_digest", None)
                != hashlib.sha256(canonical_payload({"nodes": tuple(nodes)})).hexdigest()
            ):
                raise ValueError("STRUCTURE_DIGEST_MISMATCH")
            return StructuralDocument.model_validate(
                {"artifact": self._artifact_from_row(source), "nodes": nodes, **metadata}
            )

    def read_structure_metadata_digest(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> str | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(artifact_versions.c.structure_metadata_json)
                    .join(artifacts, artifacts.c.artifact_id == artifact_versions.c.artifact_id)
                    .where(
                        artifacts.c.project_id == project_id,
                        artifacts.c.artifact_id == artifact_id,
                        artifact_versions.c.source_version_id == source_version_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return hashlib.sha256(str(row["structure_metadata_json"] or "").encode()).hexdigest()

    def update_artifact_cutoff(
        self,
        expected: ArtifactEnvelope,
        state: CutoffState,
    ) -> None:
        with write_connection(self._engine) as connection:
            changed = connection.execute(
                update(artifacts)
                .where(
                    artifacts.c.artifact_id == expected.artifact_id,
                    artifacts.c.project_id == expected.project_id,
                    artifacts.c.byte_sha256 == expected.byte_sha256,
                    artifacts.c.cutoff_state == expected.cutoff_state.value,
                )
                .values(cutoff_state=state.value)
            ).rowcount
            if changed != 1:
                raise ValueError("artifact admission basis changed")

    def read_artifact(self, artifact_id: str) -> ArtifactEnvelope | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(artifacts, artifact_versions.c.version_label)
                    .join(
                        artifact_versions,
                        artifact_versions.c.artifact_id == artifacts.c.artifact_id,
                    )
                    .where(artifacts.c.artifact_id == artifact_id)
                    .order_by(artifact_versions.c.retrieved_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return None if row is None else self._artifact_from_row(row)

    def list_artifacts(self, project_id: ProjectId) -> tuple[ArtifactEnvelope, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(artifacts, artifact_versions.c.version_label)
                    .join(
                        artifact_versions,
                        artifact_versions.c.artifact_id == artifacts.c.artifact_id,
                    )
                    .where(artifacts.c.project_id == project_id)
                    .order_by(artifacts.c.artifact_id, artifact_versions.c.retrieved_at.desc())
                )
                .mappings()
                .all()
            )
        latest: dict[str, ArtifactEnvelope] = {}
        for row in rows:
            artifact_id = str(row["artifact_id"])
            latest.setdefault(artifact_id, self._artifact_from_row(row))
        return tuple(latest[key] for key in sorted(latest))

    @staticmethod
    def _artifact_from_row(values: RowMapping) -> ArtifactEnvelope:
        return ArtifactEnvelope(
            artifact_id=str(values["artifact_id"]),
            project_id=str(values["project_id"]),
            source_uri=str(values["source_uri"]),
            media_type=str(values["media_type"]),
            byte_sha256=str(values["byte_sha256"]),
            version_label=(
                None if values["version_label"] is None else str(values["version_label"])
            ),
            authority=AuthorityState(str(values["authority"])),
            cutoff_state=CutoffState(str(values["cutoff_state"])),
            security_class=SecurityClass(str(values["security_class"])),
            retrieved_at=datetime.fromisoformat(str(values["retrieved_at"])),
            parser_name=str(values["parser_name"]),
            parser_version=str(values["parser_version"]),
        )

    def list_evidence(self, project_id: ProjectId) -> tuple[EvidenceSpan, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(evidence_spans)
                    .where(evidence_spans.c.project_id == project_id)
                    .order_by(evidence_spans.c.span_id)
                )
                .mappings()
                .all()
            )
        return tuple(self._evidence_from_row(row) for row in rows)

    def read_evidence(self, span_id: str) -> EvidenceSpan | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_spans).where(evidence_spans.c.span_id == span_id)
                )
                .mappings()
                .first()
            )
        return None if row is None else self._evidence_from_row(row)

    def update_evidence(self, span: EvidenceSpan) -> None:
        with write_connection(self._engine) as connection:
            changed = connection.execute(
                update(evidence_spans)
                .where(evidence_spans.c.span_id == span.span_id)
                .values(
                    support_state=span.support_state.value,
                    authority_state=span.authority_state.value,
                    verification_state=span.verification_state.value,
                    cutoff_state=span.cutoff_state.value,
                )
            ).rowcount
        if changed != 1:
            raise KeyError(span.span_id)

    @staticmethod
    def _evidence_from_row(values: RowMapping) -> EvidenceSpan:
        return EvidenceSpan(
            span_id=str(values["span_id"]),
            project_id=str(values["project_id"]),
            artifact_id=str(values["artifact_id"]),
            source_version_id=str(values["source_version_id"]),
            locator=SourceLocator.model_validate(orjson.loads(str(values["locator_json"]))),
            exact_text=str(values["exact_text"]),
            text_sha256=str(values["text_sha256"]),
            extraction_method=str(values["extraction_method"]),
            support_state=SupportState(str(values["support_state"])),
            authority_state=AuthorityState(str(values["authority_state"])),
            verification_state=VerificationState(str(values["verification_state"])),
            cutoff_state=CutoffState(str(values["cutoff_state"])),
        )

    def read_source_time(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> SourceTimeAssessment | None:
        document = self.read_structure(project_id, artifact_id, source_version_id)
        return None if document is None else document.source_time_assessment

    def list_source_times(self, project_id: str) -> tuple[SourceTimeAssessment, ...]:
        assessments: list[SourceTimeAssessment] = []
        for artifact in self.list_artifacts(project_id):
            versions = self.list_source_versions(project_id, artifact.artifact_id)
            if not versions:
                continue
            assessment = self.read_source_time(project_id, artifact.artifact_id, versions[-1])
            if assessment is not None:
                assessments.append(assessment)
        return tuple(assessments)

    def apply_source_time(
        self,
        basis: SourceTimeMutationBasis,
        *,
        next_state: CutoffState,
        mode: SourceTimeAssessmentMode,
        reason_code: SourceTimeReasonCode,
        assessed_at: datetime,
        require_unknown: bool,
        allow_resolved: bool,
    ) -> SourceTimeAssessment:
        import hashlib

        from sqlalchemy import select, update

        from thoth.adapters.storage.schema import artifact_versions, artifacts, evidence_spans

        with write_connection(self._engine) as connection:
            source = (
                connection.execute(
                    select(
                        artifacts,
                        artifact_versions.c.source_version_id,
                        artifact_versions.c.version_label,
                        artifact_versions.c.structure_metadata_json,
                        artifact_versions.c.byte_sha256,
                    )
                    .join(
                        artifact_versions,
                        artifact_versions.c.artifact_id == artifacts.c.artifact_id,
                    )
                    .where(
                        artifacts.c.project_id == basis.project_id,
                        artifacts.c.artifact_id == basis.artifact_id,
                        artifact_versions.c.source_version_id == basis.source_version_id,
                    )
                )
                .mappings()
                .first()
            )
            if source is None:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_VERSION_MISMATCH.value,
                    "source version was not found for time mutation",
                )
            if str(source["byte_sha256"]) != basis.byte_sha256:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_VERSION_MISMATCH.value,
                    "source bytes changed before time mutation",
                )
            current_state = CutoffState(str(source["cutoff_state"]))
            if current_state == CutoffState.PROHIBITED_CONTEXT:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_PROHIBITED.value,
                    "prohibited sources cannot be reclassified by time mutation",
                )
            if require_unknown and current_state != CutoffState.UNKNOWN_TIME:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_ALREADY_RESOLVED.value,
                    "source time is already resolved",
                )
            if (
                not allow_resolved
                and not require_unknown
                and current_state != CutoffState.UNKNOWN_TIME
            ):
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_ALREADY_RESOLVED.value,
                    "source time is already resolved",
                )
            metadata: dict[str, object] = (
                {}
                if source["structure_metadata_json"] is None
                else cast(
                    dict[str, object],
                    orjson.loads(str(source["structure_metadata_json"])),
                )
            )
            raw_metadata = str(source["structure_metadata_json"] or "")
            current_digest = hashlib.sha256(raw_metadata.encode()).hexdigest()
            if current_digest != basis.expected_metadata_digest:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_BASIS_STALE.value,
                    "source metadata changed before time mutation",
                )
            current = cast(dict[str, object] | None, metadata.get("source_time_assessment"))
            current_revision = (
                0
                if current is None
                else int(cast(int | str | float, current.get("revision", 0)))
            )
            revision = 0 if current is None else current_revision + 1
            if (
                current is not None
                and current_revision != basis.expected_assessment_revision
            ):
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_BASIS_STALE.value,
                    "source time assessment revision changed",
                )
            assessment = build_source_time_assessment(
                project_id=basis.project_id,
                artifact_id=basis.artifact_id,
                source_version_id=basis.source_version_id,
                byte_sha256=basis.byte_sha256,
                cutoff_at=basis.expected_cutoff_at,
                cutoff_state=next_state,
                mode=mode,
                basis_observation_ids=tuple(
                    cast(Iterable[str], current.get("basis_observation_ids", ()))
                    if current
                    else ()
                ),
                reason_code=reason_code,
                revision=revision,
                assessed_at=assessed_at,
            )
            metadata["source_time_assessment"] = assessment.model_dump(mode="json")
            encoded = orjson.dumps(metadata, option=orjson.OPT_SORT_KEYS).decode()
            changed = connection.execute(
                update(artifact_versions)
                .where(
                    artifact_versions.c.source_version_id == basis.source_version_id,
                    artifact_versions.c.artifact_id == basis.artifact_id,
                    artifact_versions.c.byte_sha256 == basis.byte_sha256,
                )
                .values(structure_metadata_json=encoded)
            ).rowcount
            if changed != 1:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_VERSION_MISMATCH.value,
                    "source version could not be updated",
                )
            changed = connection.execute(
                update(artifacts)
                .where(
                    artifacts.c.artifact_id == basis.artifact_id,
                    artifacts.c.project_id == basis.project_id,
                    artifacts.c.byte_sha256 == basis.byte_sha256,
                    artifacts.c.cutoff_state == current_state.value,
                )
                .values(cutoff_state=next_state.value)
            ).rowcount
            if changed != 1:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_BASIS_STALE.value,
                    "artifact cutoff changed before time mutation",
                )
            spans = connection.execute(
                update(evidence_spans)
                .where(
                    evidence_spans.c.project_id == basis.project_id,
                    evidence_spans.c.artifact_id == basis.artifact_id,
                    evidence_spans.c.source_version_id == basis.source_version_id,
                )
                .values(cutoff_state=next_state.value)
            ).rowcount
            if spans < 0:
                raise SourceTimeError(
                    SourceTimeMutationReason.SOURCE_TIME_VERSION_MISMATCH.value,
                    "evidence spans could not be updated",
                )
        return assessment
