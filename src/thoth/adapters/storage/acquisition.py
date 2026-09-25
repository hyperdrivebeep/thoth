from __future__ import annotations

from collections.abc import Callable

import orjson
from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import Connection

from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.adapters.storage.schema import (
    artifact_versions,
    artifacts,
    control_records,
    evidence_audit,
    evidence_sources,
    evidence_spans,
    project_policies,
    projects,
    source_bindings,
    structural_nodes,
)
from thoth.adapters.storage.structure_payload import structure_metadata
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork, write_connection
from thoth.domain.acquisition import AcquisitionCommit
from thoth.domain.control_record import ControlRecord
from thoth.domain.evidence_graph import EvidenceAuditRecord, EvidenceSourceRecord
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.acquisition import AcquisitionConflictError, AcquisitionUnitOfWorkPort
from thoth.ports.resource_scope import ResourceScopeAdmissionPort


def _dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


class SqliteAcquisitionUnitOfWork(AcquisitionUnitOfWorkPort):
    def __init__(
        self,
        engine: Engine,
        *,
        fault_injector: Callable[[str], None] | None = None,
        resource_scopes: ResourceScopeAdmissionPort | None = None,
    ) -> None:
        self._engine = engine
        self._fault_injector = fault_injector
        self._resource_scopes = resource_scopes

    def commit(self, value: AcquisitionCommit) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            project_before = (
                connection.execute(
                    select(projects).where(projects.c.project_id == value.project_after.project_id)
                )
                .mappings()
                .one()
            )
            authoritative = (
                connection.execute(
                    select(project_policies.c.policy_id, project_policies.c.policy_digest)
                    .where(project_policies.c.project_id == value.project_after.project_id)
                    .order_by(project_policies.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if (
                authoritative is None
                or str(authoritative["policy_id"]) != value.project_after.policy_binding_ref
                or str(authoritative["policy_digest"]) != value.connector_run.policy_digest
            ):
                raise AcquisitionConflictError(
                    "authoritative project policy changed before acquisition commit"
                )

            if self._resource_scopes is not None:
                staged = value.ingestion.resource_scope
                if (
                    staged is None
                    or staged.record.resource_ref != value.ingestion.document.artifact.artifact_id
                ):
                    raise ResourceScopeError("RESOURCE_INTAKE_RECORD_MISMATCH")
                self._resource_scopes.admit(staged)
                self._fault("after_resource_scope")
            self._insert_ingestion(connection, value)
            self._fault("after_ingestion")
            binding = value.source_binding
            connection.execute(
                insert(source_bindings).values(
                    binding_id=binding.binding_id,
                    project_id=binding.project_id,
                    artifact_id=binding.artifact_id,
                    capability=binding.capability,
                    state=binding.state,
                    created_at=binding.created_at.isoformat(),
                    updated_at=binding.updated_at.isoformat(),
                )
            )
            binding_row = (
                connection.execute(
                    select(source_bindings).where(
                        source_bindings.c.binding_id == binding.binding_id
                    )
                )
                .mappings()
                .one()
            )
            SqliteGovernanceHistory.stage(connection, "SOURCE_BINDING", dict(binding_row))
            self._insert_evidence_source(connection, value.evidence_source)
            self._insert_evidence_audit(connection, value.evidence_audit)
            self._fault("after_source_projection")

            project = value.project_after
            changed = connection.execute(
                update(projects)
                .where(
                    projects.c.project_id == project.project_id,
                    projects.c.revision == value.expected_project_revision,
                )
                .values(
                    name=project.name,
                    description=project.description,
                    cutoff_at=project.cutoff_at.isoformat(),
                    lifecycle=project.lifecycle.value,
                    overlay=project.overlay,
                    policy_ref=project.policy_binding_ref,
                    revision=project.revision,
                )
            ).rowcount
            if changed != 1:
                raise AcquisitionConflictError("project revision changed before acquisition commit")
            project_after = (
                connection.execute(
                    select(projects).where(projects.c.project_id == project.project_id)
                )
                .mappings()
                .one()
            )
            SqliteGovernanceHistory.stage(
                connection, "PROJECT", dict(project_after), before=dict(project_before)
            )
            self._fault("after_project_projection")
            self._insert_control_record(connection, value.run_record)
            self._insert_control_record(connection, value.receipt_record)
            for record in value.transformation_records:
                self._insert_control_record(connection, record)
            self._fault("after_receipts")

    def _insert_ingestion(self, connection: Connection, value: AcquisitionCommit) -> None:
        execute = connection.execute
        exec_driver_sql = connection.exec_driver_sql
        result = value.ingestion
        document = result.document
        artifact = document.artifact
        execute(
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
        execute(
            insert(artifact_versions).values(
                source_version_id=result.source_version_id,
                structure_metadata_json=structure_metadata(document),
                artifact_id=artifact.artifact_id,
                version_label=artifact.version_label,
                byte_sha256=artifact.byte_sha256,
                retrieved_at=artifact.retrieved_at.isoformat(),
            )
        )
        for node in document.nodes:
            execute(
                insert(structural_nodes).values(
                    node_id=node.node_id,
                    project_id=artifact.project_id,
                    artifact_id=artifact.artifact_id,
                    parent_id=node.parent_id,
                    kind=node.kind.value,
                    ordinal=node.ordinal,
                    text=node.text,
                    locator_json=_dump(node.locator.model_dump(mode="json")),
                    source_version_id=result.source_version_id,
                    content_json=node.model_dump_json(),
                )
            )
            if node.text:
                exec_driver_sql(
                    "INSERT INTO structural_fts(node_id, project_id, artifact_id, text) "
                    "VALUES (?, ?, ?, ?)",
                    (node.node_id, artifact.project_id, artifact.artifact_id, node.text),
                )
        for span in result.evidence_candidates:
            execute(
                insert(evidence_spans).values(
                    span_id=span.span_id,
                    project_id=span.project_id,
                    artifact_id=span.artifact_id,
                    source_version_id=span.source_version_id,
                    locator_json=_dump(span.locator.model_dump(mode="json")),
                    exact_text=span.exact_text,
                    text_sha256=span.text_sha256,
                    extraction_method=span.extraction_method,
                    support_state=span.support_state.value,
                    authority_state=span.authority_state.value,
                    verification_state=span.verification_state.value,
                    cutoff_state=span.cutoff_state.value,
                )
            )

    @staticmethod
    def _insert_evidence_source(connection: Connection, value: EvidenceSourceRecord) -> None:
        connection.execute(
            insert(evidence_sources).values(
                source_id=value.source_id,
                project_id=value.project_id,
                artifact_id=value.artifact_id,
                connector_ref=value.connector_ref,
                uri=value.uri,
                artifact_type=value.artifact_type,
                version=value.version,
                sha256=value.sha256,
                authority_status=value.authority_status.value,
                security_class=value.security_class,
                official_copy=int(value.official_copy),
                rights=value.rights,
                retention=value.retention,
                valid_time=None if value.valid_time is None else value.valid_time.isoformat(),
                snapshot_time=(
                    None if value.snapshot_time is None else value.snapshot_time.isoformat()
                ),
                cutoff_eligibility=value.cutoff_eligibility.value,
                lineage_root_id=value.lineage_root_id,
                parent_source_ids_json=_dump(value.parent_source_ids),
                supersedes_source_id=value.supersedes_source_id,
                source_digest=value.source_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    @staticmethod
    def _insert_evidence_audit(connection: Connection, value: EvidenceAuditRecord) -> None:
        connection.execute(
            insert(evidence_audit).values(
                audit_id=value.audit_id,
                project_id=value.project_id,
                evidence_ref=value.evidence_ref,
                event_type=value.event_type,
                payload_json=_dump(value.payload),
                event_digest=value.event_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    @staticmethod
    def _insert_control_record(connection: Connection, value: ControlRecord) -> None:
        connection.execute(
            insert(control_records).values(
                control_revision_id=value.control_revision_id,
                project_id=value.project_id,
                namespace=value.namespace,
                record_type=value.record_type,
                record_id=value.record_id,
                version=value.version,
                state=value.state,
                content_json=_dump(value.model_dump(mode="json")),
                record_digest=value.record_digest,
                supersedes_digest=value.supersedes_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    def _fault(self, step: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(step)
