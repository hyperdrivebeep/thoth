from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.base import DomainModel
from thoth.domain.evidence_graph import EvidenceLinkRecord
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class EvidenceListInput(ProjectInput):
    target_id: str | None = Field(default=None, max_length=160)


class EvidenceReadInput(ProjectInput):
    evidence_id: str | None = Field(default=None, max_length=160)
    span_id: str | None = Field(default=None, max_length=160)
    include: tuple[str, ...] = ("source", "span", "lineage")


class EvidencePacketInput(ProjectInput):
    target_id: str = Field(min_length=1, max_length=160)


class ConflictReadInput(ProjectInput):
    conflict_id: str = Field(min_length=1, max_length=160)


class EvidenceAuditInput(ProjectInput):
    evidence_ref: str | None = Field(default=None, max_length=160)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1_000)


class SourceAddInput(ProjectInput):
    artifact_id: str = Field(min_length=1, max_length=160)
    connector_ref: str = Field(default="project-source", min_length=1, max_length=160)
    version: str | None = Field(default=None, max_length=260)
    rights: str = Field(default="UNKNOWN", min_length=1, max_length=160)
    retention: str = Field(default="PROJECT_DEFAULT", min_length=1, max_length=160)


class SourceRefreshInput(SourceAddInput):
    source_id: str = Field(min_length=1, max_length=160)


class SourceMetadataCorrectInput(ProjectInput):
    source_id: str = Field(min_length=1, max_length=160)
    rights: str | None = Field(default=None, max_length=160)
    retention: str | None = Field(default=None, max_length=160)
    official_copy_basis: str | None = Field(default=None, max_length=2_000)


class SpanCorrectInput(ProjectInput):
    span_id: str = Field(min_length=1, max_length=160)
    corrected_text: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=1, max_length=2_000)


class LinkProposeInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    target_type: str = Field(min_length=1, max_length=80)
    target_id: str = Field(min_length=1, max_length=160)
    relation: str = Field(min_length=1, max_length=40)
    span_ids: tuple[str, ...]
    observed_statement: str = Field(min_length=1, max_length=100_000)
    conditions: dict[str, str] = Field(default_factory=dict)
    applicability: str = Field(default="WITHIN_STATED_CONDITIONS", max_length=2_000)
    independence_group: str = Field(min_length=1, max_length=160)


class LinkCorrectInput(LinkProposeInput):
    evidence_id: str = Field(min_length=1, max_length=160)


class ChallengeInput(ProjectInput):
    evidence_ids: tuple[str, ...]
    field: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=10_000)


class RevalidateInput(ProjectInput):
    evidence_id: str = Field(min_length=1, max_length=160)


class EvidenceCommandHandlers:
    def __init__(
        self,
        *,
        store: EvidenceGraphStorePort,
        service: EvidenceGraphService,
        artifacts: ArtifactLedgerPort,
    ) -> None:
        self._store = store
        self._service = service
        self._artifacts = artifacts

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvidenceListInput.model_validate(value)
        links = self._store.list_links(request.project_id, request.target_id)
        spans = self._artifacts.list_evidence(request.project_id)
        return {
            "items": [item.model_dump(mode="json") for item in links],
            "links": [item.model_dump(mode="json") for item in links],
            "evidence": [span.model_dump(mode="json") for span in spans],
            "spans": [span.model_dump(mode="json") for span in spans],
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvidenceReadInput.model_validate(value)
        if request.evidence_id is not None:
            link = self._store.read_link(request.evidence_id)
            if link is None or link.project_id != request.project_id:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "evidence not found")
            sources: list[JsonValue] = [
                cast(JsonValue, source.model_dump(mode="json"))
                for source_id in link.source_ids
                if (source := self._store.read_source(source_id)) is not None
            ]
            spans: list[JsonValue] = [
                cast(JsonValue, span.model_dump(mode="json"))
                for span_id in link.span_ids
                if (span := self._artifacts.read_evidence(span_id)) is not None
            ]
            return {
                "evidence": link.model_dump(mode="json"),
                "sources": sources,
                "spans": spans,
            }
        if request.span_id is not None:
            span = self._artifacts.read_evidence(request.span_id)
            if span is None or span.project_id != request.project_id:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "span not found")
            source = self._store.read_source_by_artifact(span.artifact_id)
            return {
                "span": span.model_dump(mode="json"),
                "evidence": span.model_dump(mode="json"),
                "source": None if source is None else source.model_dump(mode="json"),
                "corrections": [
                    item.model_dump(mode="json")
                    for item in self._store.list_span_corrections(request.project_id, span.span_id)
                ],
            }
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "evidence_id or span_id is required")

    async def packet_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvidencePacketInput.model_validate(value)
        links = self._store.list_links(request.project_id, request.target_id)
        link_ids = {item.evidence_id for item in links}
        conflicts = tuple(
            conflict
            for conflict in self._store.list_conflicts(request.project_id)
            if link_ids & set(conflict.evidence_ids)
        )
        return {
            "packet": {
                "project_id": request.project_id,
                "target_id": request.target_id,
                "support": [
                    item.model_dump(mode="json") for item in links if item.relation == "SUPPORTS"
                ],
                "counterevidence": [
                    item.model_dump(mode="json") for item in links if item.relation == "CONTRADICTS"
                ],
                "conflicts": [item.model_dump(mode="json") for item in conflicts],
                "authority_gaps": [
                    item.evidence_id for item in links if item.authority_status == "UNCLASSIFIED"
                ],
                "verification_failures": [
                    item.evidence_id
                    for item in links
                    if item.verification_status == "VERIFICATION_FAILED"
                ],
            }
        }

    async def conflict_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "conflicts": [
                item.model_dump(mode="json")
                for item in self._store.list_conflicts(request.project_id)
            ]
        }

    async def conflict_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ConflictReadInput.model_validate(value)
        conflict = self._store.read_conflict(request.conflict_id)
        if conflict is None or conflict.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "conflict not found")
        return {"conflict": conflict.model_dump(mode="json")}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvidenceAuditInput.model_validate(value)
        records = self._store.list_audit(
            request.project_id,
            request.evidence_ref,
            offset=request.offset,
            limit=request.limit,
        )
        return {"records": [item.model_dump(mode="json") for item in records]}

    async def source_add(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceAddInput.model_validate(value)
        artifact = self._artifact(request.project_id, request.artifact_id)
        try:
            source = self._service.register_source(
                artifact,
                connector_ref=request.connector_ref,
                version=request.version,
                rights=request.rights,
                retention=request.retention,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"source": source.model_dump(mode="json")}

    async def source_refresh(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceRefreshInput.model_validate(value)
        artifact = self._artifact(request.project_id, request.artifact_id)
        try:
            source = self._service.register_source(
                artifact,
                connector_ref=request.connector_ref,
                version=request.version,
                rights=request.rights,
                retention=request.retention,
                supersedes_source_id=request.source_id,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"source": source.model_dump(mode="json")}

    async def source_metadata_correct(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceMetadataCorrectInput.model_validate(value)
        try:
            source = self._service.correct_source_metadata(
                project_id=request.project_id,
                source_id=request.source_id,
                rights=request.rights,
                retention=request.retention,
                official_copy_basis=request.official_copy_basis,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"source": source.model_dump(mode="json")}

    async def span_correct(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SpanCorrectInput.model_validate(value)
        try:
            correction = self._service.correct_span(
                request.project_id,
                request.span_id,
                request.corrected_text,
                request.reason,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"correction": correction.model_dump(mode="json")}

    async def link_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = LinkProposeInput.model_validate(value)
        return {"evidence": self._propose(request).model_dump(mode="json")}

    async def link_correct(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = LinkCorrectInput.model_validate(value)
        return {
            "evidence": self._propose(
                request, supersedes_evidence_id=request.evidence_id
            ).model_dump(mode="json")
        }

    async def challenge(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChallengeInput.model_validate(value)
        try:
            conflict = self._service.challenge(
                project_id=request.project_id,
                evidence_ids=request.evidence_ids,
                field=request.field,
                reason=request.reason,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"conflict": conflict.model_dump(mode="json")}

    async def revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevalidateInput.model_validate(value)
        try:
            evidence = self._service.revalidate(request.project_id, request.evidence_id)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {"evidence": evidence.model_dump(mode="json")}

    def _propose(
        self,
        request: LinkProposeInput,
        *,
        supersedes_evidence_id: str | None = None,
    ) -> EvidenceLinkRecord:
        try:
            return self._service.propose_link(
                project_id=request.project_id,
                target_type=request.target_type,
                target_id=request.target_id,
                relation=request.relation,
                span_ids=request.span_ids,
                observed_statement=request.observed_statement,
                thread_id=request.thread_id,
                conditions=request.conditions,
                applicability=request.applicability,
                independence_group=request.independence_group,
                supersedes_evidence_id=supersedes_evidence_id,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    def _artifact(self, project_id: str, artifact_id: str) -> ArtifactEnvelope:
        artifact = next(
            (
                item
                for item in self._artifacts.list_artifacts(project_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "artifact not found")
        return artifact
