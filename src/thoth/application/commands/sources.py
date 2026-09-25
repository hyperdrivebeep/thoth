from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from thoth.application.services import (
    ConnectorAcquisition,
    ConnectorService,
    IngestArtifactCommand,
    IngestionService,
)
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.resource_scope_context import (
    resource_intake_scope,
    resource_stage_scope,
)
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.application.services.source_time_service import SourceTimeService
from thoth.domain.artifact import ParserSelection
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.errors import ParserFailure
from thoth.domain.governance import SourceBinding
from thoth.domain.policy import PolicyExpectation
from thoth.domain.project import Project
from thoth.domain.resource_scope import ResourceScopeError, ResourceScopeTemplate
from thoth.domain.source_time import (
    SourceTimeAssertion,
    SourceTimeError,
    SourceTimeMutationBasis,
)
from thoth.domain.upload_scope import project_upload_path_allowed
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class SourceConnectInput(DomainModel):
    parser_selection: ParserSelection | None = None
    project_id: str = Field(min_length=1, max_length=160)
    connector_id: str | None = Field(default=None, min_length=1, max_length=160)
    relative_path: str | None = Field(default=None, min_length=1, max_length=500)
    selector: dict[str, JsonValue] = Field(default_factory=dict)
    media_type: str = Field(min_length=1, max_length=260)
    authority: AuthorityState = AuthorityState.UNCLASSIFIED
    cutoff_state: CutoffState = CutoffState.UNKNOWN_TIME
    security_class: SecurityClass = SecurityClass.INTERNAL
    version_label: str | None = Field(default=None, max_length=260)
    expected_project_revision: int | None = Field(default=None, ge=0)
    expected_policy_id: str | None = Field(default=None, min_length=1, max_length=160)
    expected_policy_revision: int | None = Field(default=None, ge=1)
    expected_policy_digest: str | None = Field(default=None, min_length=64, max_length=64)
    resource_scope: ResourceScopeTemplate | None = None

    @model_validator(mode="after")
    def require_selector(self) -> SourceConnectInput:
        if self.relative_path is None and not self.selector:
            raise ValueError("source connection requires relative_path or selector")
        policy_fields = (
            self.expected_policy_id,
            self.expected_policy_revision,
            self.expected_policy_digest,
        )
        if any(value is not None for value in policy_fields) and not all(
            value is not None for value in policy_fields
        ):
            raise ValueError("expected policy ID, revision and digest must be supplied together")
        return self


class ProjectScopedInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class EvidenceReadInput(ProjectScopedInput):
    span_id: str = Field(min_length=1, max_length=160)


class SourceDisconnectInput(ProjectScopedInput):
    binding_id: str = Field(min_length=1, max_length=160)
    mode: str = Field(pattern=r"^(DETACH|REVOKE)$")
    expected_project_revision: int | None = Field(default=None, ge=0)


class SourceTimeMutationInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    artifact_id: str = Field(min_length=1, max_length=160)
    source_version_id: str = Field(min_length=1, max_length=160)
    byte_sha256: str = Field(min_length=64, max_length=64)
    expected_project_revision: int = Field(ge=0)
    expected_cutoff_at: AwareDatetime
    expected_assessment_revision: int = Field(ge=0)
    expected_metadata_digest: str = Field(min_length=64, max_length=64)


class SourceTimeConfirmInput(SourceTimeMutationInput):
    assertion: SourceTimeAssertion


class SourceTimeCorrectInput(SourceTimeMutationInput):
    assertion: SourceTimeAssertion | None = None
    revert_unknown: bool = False
    correction_reason: str = Field(min_length=1, max_length=500)


class SourceCommandHandlers:
    def __init__(
        self,
        *,
        inbox_root: Path,
        ingestion: IngestionService,
        artifacts: ArtifactLedgerPort,
        governance: GovernanceStorePort,
        projects: ProjectStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
        evidence_graph: EvidenceGraphService,
        ids: IdGeneratorPort,
        connectors: ConnectorService | None = None,
        resource_scopes: ResourceScopeService | None = None,
        source_time: SourceTimeService | None = None,
    ) -> None:
        self._inbox_root = inbox_root.resolve()
        self._ingestion = ingestion
        self._artifacts = artifacts
        self._governance = governance
        self._projects = projects
        self._ledger = ledger
        self._clock = clock
        self._evidence_graph = evidence_graph
        self._ids = ids
        self._connectors = connectors
        self._resource_scopes = resource_scopes
        self._source_time = source_time

    async def connect(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceConnectInput.model_validate(value)
        if self._resource_scopes is None:
            raise ResourceScopeError("RESOURCE_SCOPE_REQUIRED")
        basis = self._resource_scopes.prepare_intake(request.project_id, request.resource_scope)
        try:
            with resource_intake_scope(basis):
                return await self._connect(value)
        except ParserFailure as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "source parsing failed",
                data={"parser_error": exc.code.value},
            ) from exc

    async def _connect(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceConnectInput.model_validate(value)
        acquisition: ConnectorAcquisition | None = None
        project = self._projects.read(request.project_id)
        if (
            project is not None
            and request.expected_project_revision is not None
            and project.revision != request.expected_project_revision
        ):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "project revision changed before source connection",
            )
        if self._connectors is not None:
            selector = dict(request.selector)
            if request.relative_path is not None:
                selector.setdefault("relative_path", request.relative_path)
            try:
                connector_id = request.connector_id or self._connectors.route(
                    cast(dict[str, object], selector)
                )
            except ConnectorFailure as exc:
                raise self._connector_error(exc) from exc
            authenticated = current_authenticated_actor()
            relative_selector = selector.get("relative_path")
            if (
                connector_id == "local-file-upload"
                and (
                    authenticated is not None
                    or (
                        isinstance(relative_selector, str)
                        and relative_selector.replace("\\", "/").startswith("web/")
                    )
                )
                and (
                    (authenticated is not None and authenticated.project_id != request.project_id)
                    or not isinstance(relative_selector, str)
                    or not project_upload_path_allowed(request.project_id, relative_selector)
                )
            ):
                raise RpcApplicationError(
                    RpcErrorCode.AUTHORIZATION_DENIED,
                    "upload path is outside the project inbox",
                    data={"reason_code": "AUTH_UPLOAD_PROJECT_SCOPE_DENIED", "pre_io": True},
                )
            try:
                expectation = (
                    self._connectors.current_policy_expectation(request.project_id)
                    if request.expected_policy_id is None
                    else PolicyExpectation(
                        policy_id=request.expected_policy_id,
                        policy_revision=cast(int, request.expected_policy_revision),
                        policy_digest=cast(str, request.expected_policy_digest),
                    )
                )
            except ConnectorFailure as exc:
                raise self._connector_error(exc) from exc
            try:
                acquisition = await self._connectors.acquire_one(
                    ConnectorAccessRequest(
                        parser_selection=request.parser_selection,
                        actor_id="agent:authorized-evidence-acquisition",
                        project_id=request.project_id,
                        connector_id=connector_id,
                        selector=selector,
                        authority=request.authority,
                        cutoff_state=request.cutoff_state,
                        security_class=request.security_class,
                        cutoff_at=None if project is None else project.cutoff_at,
                        policy_id=expectation.policy_id,
                        policy_revision=expectation.policy_revision,
                        policy_digest=expectation.policy_digest,
                    )
                )
            except ConnectorFailure as exc:
                raise self._connector_error(exc) from exc
            result = acquisition.ingestion
            binding = acquisition.binding
            source_record = acquisition.source
        else:
            if request.relative_path is None:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "legacy source connection requires relative_path",
                )
            relative = Path(request.relative_path)
            path = (self._inbox_root / relative).resolve()
            if (
                relative.is_absolute()
                or not path.is_relative_to(self._inbox_root)
                or not path.is_file()
            ):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "source path is missing or outside the workspace inbox",
                    data={"relative_path": request.relative_path},
                )
            result = await self._ingestion.stage_async(
                IngestArtifactCommand(
                    project_id=request.project_id,
                    source_uri=f"inbox://{relative.as_posix()}",
                    media_type=request.media_type,
                    raw=path.read_bytes(),
                    authority=request.authority,
                    cutoff_state=request.cutoff_state,
                    security_class=request.security_class,
                    operation_id=self._ids.new("ingest-operation"),
                    version_label=request.version_label,
                    source_path=path,
                    parser_selection=request.parser_selection,
                    classify_source_time=True,
                    captured_cutoff_at=None if project is None else project.cutoff_at,
                    captured_project_revision=None if project is None else project.revision,
                )
            )
            with self._ledger.transaction(), resource_stage_scope(result.resource_scope):
                self._artifacts.persist_ingestion(
                    result.document, result.source_version_id, result.evidence_candidates
                )
                binding = SourceBinding(
                    binding_id=self._ids.new("source-binding"),
                    project_id=request.project_id,
                    artifact_id=result.document.artifact.artifact_id,
                    capability="READ",
                    state="ACTIVE",
                    created_at=self._clock.now(),
                    updated_at=self._clock.now(),
                )
                self._governance.put_source_binding(binding)
                source_record = self._evidence_graph.register_source(
                    result.document.artifact,
                    connector_ref="project/source/connect",
                    version=result.document.artifact.version_label,
                )
                if project is not None:
                    active_ids = tuple(
                        item.binding_id
                        for item in self._governance.list_source_bindings(request.project_id)
                        if item.state == "ACTIVE"
                    )
                    self._update_project(
                        project.model_copy(
                            update={
                                "source_binding_ids": active_ids,
                                "revision": project.revision + 1,
                            }
                        ),
                        expected_revision=project.revision,
                    )
        assessment = result.document.source_time_assessment
        return {
            "artifact": result.document.artifact.model_dump(mode="json"),
            "source_version_id": result.source_version_id,
            "evidence_count": len(result.evidence_candidates),
            "coverage": result.document.extraction_coverage,
            "warning_codes": [warning.code.value for warning in result.document.warnings],
            "binding": binding.model_dump(mode="json"),
            "source": source_record.model_dump(mode="json"),
            "source_time": None if assessment is None else assessment.model_dump(mode="json"),
            "connector_run": (
                None if acquisition is None else acquisition.run.model_dump(mode="json")
            ),
            "connector_receipt": (
                None if acquisition is None else acquisition.receipt.model_dump(mode="json")
            ),
        }

    async def list_sources(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectScopedInput.model_validate(value)
        sources = self._artifacts.list_artifacts(request.project_id)
        visible = {source.artifact_id for source in sources}
        times: list[JsonValue] = []
        if self._source_time is not None:
            for item in self._artifacts.list_source_times(request.project_id):
                payload = item.model_dump(mode="json")
                metadata_digest = self._artifacts.read_structure_metadata_digest(
                    request.project_id, item.artifact_id, item.source_version_id
                )
                if metadata_digest is not None:
                    payload["metadata_digest"] = metadata_digest
                times.append(payload)
        project = self._projects.read(request.project_id)
        return {
            "artifacts": [source.model_dump(mode="json") for source in sources],
            "bindings": [
                binding.model_dump(mode="json")
                for binding in self._governance.list_source_bindings(request.project_id)
                if binding.artifact_id in visible
            ],
            "connector_capabilities": (
                [] if self._connectors is None else list(self._connectors.capabilities())
            ),
            "source_times": times,
            "cutoff_basis": None
            if project is None
            else {
                "cutoff_at": project.cutoff_at.isoformat(),
                "project_revision": project.revision,
            },
        }

    async def confirm_time(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceTimeConfirmInput.model_validate(value)
        if self._source_time is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "source time service is unavailable"
            )
        try:
            assessment = self._source_time.confirm_unknown(
                SourceTimeMutationBasis(
                    project_id=request.project_id,
                    artifact_id=request.artifact_id,
                    source_version_id=request.source_version_id,
                    byte_sha256=request.byte_sha256,
                    expected_project_revision=request.expected_project_revision,
                    expected_cutoff_at=request.expected_cutoff_at,
                    expected_assessment_revision=request.expected_assessment_revision,
                    expected_metadata_digest=request.expected_metadata_digest,
                ),
                request.assertion,
            )
        except SourceTimeError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                exc.message,
                data={"reason_code": exc.reason_code},
            ) from exc
        return {"source_time": assessment.model_dump(mode="json")}

    async def correct_time(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SourceTimeCorrectInput.model_validate(value)
        if self._source_time is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "source time service is unavailable"
            )
        try:
            assessment = self._source_time.correct_time(
                SourceTimeMutationBasis(
                    project_id=request.project_id,
                    artifact_id=request.artifact_id,
                    source_version_id=request.source_version_id,
                    byte_sha256=request.byte_sha256,
                    expected_project_revision=request.expected_project_revision,
                    expected_cutoff_at=request.expected_cutoff_at,
                    expected_assessment_revision=request.expected_assessment_revision,
                    expected_metadata_digest=request.expected_metadata_digest,
                ),
                assertion=request.assertion,
                revert_unknown=request.revert_unknown,
                correction_reason=request.correction_reason,
            )
        except SourceTimeError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                exc.message,
                data={"reason_code": exc.reason_code},
            ) from exc
        return {"source_time": assessment.model_dump(mode="json")}

    async def disconnect(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._ledger.transaction():
            request = SourceDisconnectInput.model_validate(value)
            project = self._projects.read(request.project_id)
            if (
                project is not None
                and request.expected_project_revision is not None
                and project.revision != request.expected_project_revision
            ):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "project revision changed before source disconnection",
                )
            try:
                binding = self._governance.set_source_binding_state(
                    request.project_id,
                    request.binding_id,
                    state="DETACHED" if request.mode == "DETACH" else "REVOKED",
                    updated_at=self._clock.now().isoformat(),
                )
            except KeyError as exc:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    "source binding was not found in this project",
                ) from exc
            if project is not None:
                active_ids = tuple(
                    item.binding_id
                    for item in self._governance.list_source_bindings(request.project_id)
                    if item.state == "ACTIVE"
                )
                self._update_project(
                    project.model_copy(
                        update={
                            "source_binding_ids": active_ids,
                            "revision": project.revision + 1,
                        }
                    ),
                    expected_revision=project.revision,
                )
            return {"binding": binding.model_dump(mode="json")}

    async def list_evidence(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectScopedInput.model_validate(value)
        evidence = self._artifacts.list_evidence(request.project_id)
        return {"evidence": [span.model_dump(mode="json") for span in evidence]}

    async def read_evidence(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = EvidenceReadInput.model_validate(value)
        span = self._artifacts.read_evidence(request.span_id)
        if span is None or span.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "evidence span was not found in this project",
                data={"span_id": request.span_id},
            )
        return {"evidence": span.model_dump(mode="json")}

    def _update_project(self, project: Project, *, expected_revision: int) -> None:
        if not self._projects.update(project, expected_revision=expected_revision):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "project revision changed during source mutation",
                data={"reason_code": "SOURCE_PROJECT_REVISION_CONFLICT"},
            )

    @staticmethod
    def _connector_error(exc: ConnectorFailure) -> RpcApplicationError:
        data: dict[str, JsonValue] = {"connector_error": exc.code.value}
        if exc.parser_error is not None:
            data["parser_error"] = exc.parser_error.value
        denial = exc.policy_denial
        if denial is not None and hasattr(denial, "model_dump"):
            data["policy_denial"] = cast(JsonValue, denial.model_dump(mode="json"))
        return RpcApplicationError(
            RpcErrorCode.DOMAIN_REJECTED,
            str(exc),
            data=data,
        )
