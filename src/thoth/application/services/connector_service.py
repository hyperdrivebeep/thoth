from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from thoth.application.services.connector_cleanup import connector_cleanup
from thoth.application.services.connector_io import connector_io
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.ingestion_service import IngestArtifactCommand, IngestionService
from thoth.application.services.policy_gate import PolicyDenied, PolicyGate
from thoth.application.services.resource_scope_context import resource_intake_scope
from thoth.domain.acquisition import AcquisitionCommit
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorCapability,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorFetchResult,
    ConnectorOperation,
    ConnectorReceipt,
    ConnectorRunRecord,
)
from thoth.domain.enums import SecurityClass
from thoth.domain.errors import ParserFailure
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.governance import SourceBinding
from thoth.domain.ingestion import IngestionResult
from thoth.domain.policy import (
    AuthoritativeExecutionPolicy,
    PolicyDenialBasis,
    PolicyExpectation,
)
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.registered_site_entrypoints import registered_entrypoints
from thoth.domain.research_execution import check_research_boundary
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.acquisition import AcquisitionConflictError, AcquisitionUnitOfWorkPort
from thoth.ports.connectors import ConnectorPort, ConnectorRegistryPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def public_web_search_attempts(
    request: ConnectorAccessRequest,
    policy: AuthoritativeExecutionPolicy,
) -> tuple[ConnectorAccessRequest, ...]:
    if request.selector.get("mode") != "SEARCH":
        return (request,)
    if not policy.public_web_enabled or not policy.preferred_hosts:
        return (request,)
    query = request.selector.get("query")
    if not isinstance(query, str) or not query.strip():
        return (request,)
    sites = " OR ".join(f"site:{host}" for host in policy.preferred_hosts)
    scoped_selector = dict(request.selector)
    scoped_selector["query"] = f"({query}) ({sites})"
    scoped = request.model_copy(update={"selector": scoped_selector})
    return (scoped, request)


_SECURITY_ORDER = {
    SecurityClass.PUBLIC: 0,
    SecurityClass.INTERNAL: 1,
    SecurityClass.CONFIDENTIAL: 2,
    SecurityClass.RESTRICTED: 3,
}


@dataclass(frozen=True)
class ConnectorAcquisition:
    ingestion: IngestionResult
    run: ConnectorRunRecord
    receipt: ConnectorReceipt
    binding: SourceBinding
    source: EvidenceSourceRecord


class ConnectorService:
    def __init__(
        self,
        *,
        registry: ConnectorRegistryPort,
        ingestion: IngestionService,
        evidence_graph: EvidenceGraphService,
        projects: ProjectStorePort,
        policies: PolicyGate,
        unit_of_work: AcquisitionUnitOfWorkPort,
        controls: ControlRecordService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        workspace_setup: Callable[[], WorkspaceSetupState] | None = None,
    ) -> None:
        self._registry = registry
        self._ingestion = ingestion
        self._evidence_graph = evidence_graph
        self._projects = projects
        self._policies = policies
        self._unit_of_work = unit_of_work
        self._controls = controls
        self._clock = clock
        self._ids = ids
        self._workspace_setup = workspace_setup or WorkspaceSetupState

    def capabilities(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            cast(dict[str, JsonValue], item.model_dump(mode="json"))
            for item in self._registry.capabilities()
        )

    def route(self, selector: dict[str, object]) -> str:
        validated = cast(dict[str, JsonValue], selector)
        self._reject_raw_credentials(validated)
        return self._registry.route(validated).connector_id

    def current_policy_expectation(self, project_id: str) -> PolicyExpectation:
        return self._authorize(project_id, expectation=None).expectation

    def research_catalog(self, project_id: str) -> tuple[dict[str, JsonValue], ...]:
        policy = self._authorize(project_id, expectation=None)
        setup = self._workspace_setup()
        grant_ok = (
            setup.internet_consent == "ALLOWED"
            and bool(setup.internet_grant_id)
            and setup.internet_grant_id == policy.workspace_internet_grant_id
        )
        web_ready = (
            policy.public_web_enabled
            and bool(policy.preferred_hosts)
            and grant_ok
            and PROJECT_PUBLIC_WEB_CONNECTOR_ID in policy.connector_allowlist
            and "ALLOWLISTED_EXTERNAL" in policy.connector_allowed_egress_classes
        )
        return tuple(
            cast(
                dict[str, JsonValue],
                {
                    **item,
                    "index_freshness": "UNKNOWN",
                    "unvisited_scope": "NOT_ENUMERATED",
                    "configured_selectors": _catalog_selectors(item["connector_id"], policy),
                },
            )
            for item in self.capabilities()
            if item["connector_id"] in policy.connector_allowlist
            and item["egress_class"] in policy.connector_allowed_egress_classes
            and (item["connector_id"] != PROJECT_PUBLIC_WEB_CONNECTOR_ID or web_ready)
        )

    async def discover_candidates(self, request: ConnectorAccessRequest):
        policy = self._authorize(
            request.project_id,
            expectation=PolicyExpectation(
                policy_id=request.policy_id,
                policy_revision=request.policy_revision,
                policy_digest=request.policy_digest,
            ),
        )
        self._preflight_request(request, policy)
        connector = self._registry.resolve(request.connector_id)
        self._preflight_capability(request, connector.capability, policy)
        if not connector.capability.selector_contract.accepts(request.selector):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "selector contract mismatch")
        if request.connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID:
            attempts = (request,)
        else:
            attempts = public_web_search_attempts(request, policy)
        try:
            refs: tuple[object, ...] = ()
            for attempt in attempts:
                refs = await connector_io(
                    attempt,
                    "DISCOVER",
                    lambda bound=attempt: connector.discover(bound),
                    self._controls,
                )
                if refs:
                    break
        finally:
            await self._close_after_failure(connector, self._ids.new("discovery"), request)
        check_research_boundary()
        return refs[:8]

    async def acquire_one(self, request: ConnectorAccessRequest) -> ConnectorAcquisition:
        scope = self._ingestion.prepare_resource_scope(request.project_id)
        with resource_intake_scope(scope):
            return await self._acquire_one(request)

    async def _acquire_one(self, request: ConnectorAccessRequest) -> ConnectorAcquisition:
        expectation = PolicyExpectation(
            policy_id=request.policy_id,
            policy_revision=request.policy_revision,
            policy_digest=request.policy_digest,
        )
        policy = self._authorize(request.project_id, expectation=expectation)
        project = self._projects.read(request.project_id)
        if project is None:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "connector project does not exist",
            )
        if project.policy_binding_ref != policy.policy_id:
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.POLICY_BINDING_MISMATCH,
                message="project is not bound to the authoritative policy revision",
                authoritative=policy,
                expectation=expectation,
            )
        self._preflight_request(request, policy)
        connector = self._registry.resolve(request.connector_id)
        self._preflight_capability(request, connector.capability, policy)
        if not connector.capability.selector_contract.accepts(request.selector):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "selector contract mismatch")

        run_id = self._ids.new("connector-run")
        scope_digest = domain_digest(
            "CONNECTOR_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "project_id": request.project_id,
                    "connector_id": request.connector_id,
                    "operation": request.operation.value,
                    "selector": request.selector,
                    "cutoff_at": request.cutoff_at,
                    "policy_id": policy.policy_id,
                    "policy_revision": policy.policy_revision,
                    "policy_digest": policy.policy_digest,
                }
            ),
        )
        started_at = self._clock.now()
        closed = False
        try:
            refs = await connector_io(
                request, "DISCOVER", lambda: connector.discover(request), self._controls
            )
            check_research_boundary()
            if len(refs) != 1:
                raise ConnectorFailure(
                    ConnectorErrorCode.PARTIAL_FETCH,
                    "exact acquisition requires one discovered artifact",
                )
            fetched = await connector_io(
                request, "FETCH", lambda: connector.fetch(request, refs[0]), self._controls
            )
            check_research_boundary()
            actual_digest = hashlib.sha256(fetched.raw).hexdigest()
            if actual_digest != fetched.content_sha256:
                raise ConnectorFailure(
                    ConnectorErrorCode.SOURCE_MUTATED,
                    "connector content hash does not match fetched bytes",
                )
            if len(fetched.raw) > request.max_bytes:
                raise ConnectorFailure(
                    ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED,
                    "connector returned content above declared limit",
                )
            original_ingestion, ingestion = await self._stage_acquired(request, fetched)
            binding = SourceBinding(
                binding_id=self._ids.new("source-binding"),
                project_id=request.project_id,
                artifact_id=ingestion.document.artifact.artifact_id,
                capability="READ",
                state="ACTIVE",
                created_at=self._clock.now(),
                updated_at=self._clock.now(),
            )
            source, source_audit = self._evidence_graph.stage_source(
                ingestion.document.artifact,
                connector_ref=request.connector_id,
                version=ingestion.document.artifact.version_label,
            )
            if source_audit is None:
                raise ValueError("new connector artifact unexpectedly reused an evidence source")
            receipt = self._receipt(
                request=request,
                capability=connector.capability,
                run_id=run_id,
                fetched=fetched,
            )
            completed = ConnectorRunRecord(
                connector_run_id=run_id,
                project_id=request.project_id,
                connector_id=request.connector_id,
                operation=request.operation,
                state="SUCCEEDED",
                requested_scope_digest=scope_digest,
                policy_digest=policy.policy_digest,
                artifact_refs=(ingestion.document.artifact.artifact_id,),
                checkpoint_after=fetched.checkpoint_after,
                started_at=started_at,
                completed_at=self._clock.now(),
            )
            run_record = self._controls.stage(
                project_id=request.project_id,
                namespace="CONNECTOR",
                record_type="RUN",
                state=completed.state,
                payload=completed.model_dump(mode="json"),
                record_id=run_id,
            )
            receipt_record = self._controls.stage(
                project_id=request.project_id,
                namespace="CONNECTOR",
                record_type="RECEIPT",
                state="SEALED",
                payload=receipt.model_dump(mode="json"),
                record_id=f"connector-receipt:{run_id}",
            )
            project_after = project.model_copy(
                update={
                    "source_binding_ids": (*project.source_binding_ids, binding.binding_id),
                    "revision": project.revision + 1,
                }
            )
            await connector_cleanup(
                request, run_id, lambda: connector.close(run_id), self._controls
            )
            closed = True
            self._unit_of_work.commit(
                AcquisitionCommit(
                    ingestion=ingestion,
                    source_binding=binding,
                    evidence_source=source,
                    evidence_audit=source_audit,
                    project_after=project_after,
                    expected_project_revision=project.revision,
                    connector_run=completed,
                    connector_receipt=receipt,
                    run_record=run_record,
                    receipt_record=receipt_record,
                    transformation_records=()
                    if fetched.transformation is None
                    else (
                        self._controls.stage(
                            project_id=request.project_id,
                            namespace="CONNECTOR",
                            record_type="WEB_TRANSFORMATION",
                            state="ACQUIRED",
                            payload={
                                **fetched.transformation.model_dump(mode="json"),
                                "http_artifact_ref": None
                                if original_ingestion is None
                                else original_ingestion.document.artifact.artifact_id,
                                "rendered_artifact_ref": ingestion.document.artifact.artifact_id,
                            },
                        ),
                    ),
                )
            )
            return ConnectorAcquisition(
                ingestion=ingestion,
                run=completed,
                receipt=receipt,
                binding=binding,
                source=source,
            )
        except asyncio.CancelledError:
            if not closed:
                await self._close_after_failure(connector, run_id, request)
            raise
        except ConnectorFailure:
            if not closed:
                await self._close_after_failure(connector, run_id, request)
            raise
        except ParserFailure as exc:
            if not closed:
                await self._close_after_failure(connector, run_id, request)
            raise ConnectorFailure(
                ConnectorErrorCode.PARSER_FAILURE, "source parsing failed", parser_error=exc.code
            ) from exc
        except AcquisitionConflictError as exc:
            if not closed:
                await self._close_after_failure(connector, run_id, request)
            raise ConnectorFailure(
                ConnectorErrorCode.CHECKPOINT_INVALID,
                "connector acquisition state changed before atomic commit",
            ) from exc
        except Exception as exc:
            if not closed:
                await self._close_after_failure(connector, run_id, request)
            raise ConnectorFailure(
                ConnectorErrorCode.DRIVER_ERROR,
                "connector acquisition failed",
            ) from exc

    async def _stage_acquired(
        self, request: ConnectorAccessRequest, fetched: ConnectorFetchResult
    ) -> tuple[IngestionResult | None, IngestionResult]:
        original_ingestion = None
        if fetched.original_http is not None:
            original = fetched.original_http
            # The acquired shell is retained as provenance, never extracted evidence.
            classify = request.connector_id == "local-file-upload"
            project = self._projects.read(request.project_id)
            captured_cutoff = None if project is None else project.cutoff_at
            captured_revision = None if project is None else project.revision
            original_ingestion = self._ingestion.ingest(
                IngestArtifactCommand(
                    project_id=request.project_id,
                    source_uri=original.final_uri,
                    media_type=original.media_type,
                    raw=original.raw,
                    authority=request.authority,
                    cutoff_state=request.cutoff_state,
                    security_class=request.security_class,
                    operation_id=self._ids.new("http-provenance"),
                    version_label="http:" + hashlib.sha256(original.raw).hexdigest(),
                    classify_source_time=False,
                    captured_cutoff_at=captured_cutoff,
                    captured_project_revision=captured_revision,
                ),
                provenance_only=True,
            )
        classify = request.connector_id == "local-file-upload"
        project = self._projects.read(request.project_id)
        captured_cutoff = None if project is None else project.cutoff_at
        captured_revision = None if project is None else project.revision
        ingestion = await self._ingestion.stage_async(
            IngestArtifactCommand(
                project_id=request.project_id,
                source_uri=fetched.ref.source_uri,
                media_type=fetched.ref.media_type,
                raw=fetched.raw,
                authority=request.authority,
                cutoff_state=request.cutoff_state,
                security_class=request.security_class,
                operation_id=self._ids.new("connector-ingest"),
                version_label=fetched.ref.native_version.value,
                parser_selection=request.parser_selection,
                classify_source_time=classify,
                captured_cutoff_at=captured_cutoff,
                captured_project_revision=captured_revision,
            ),
            derived_parents=None
            if original_ingestion is None
            else (original_ingestion.document.artifact.artifact_id,),
        )
        return original_ingestion, ingestion

    def _authorize(
        self,
        project_id: str,
        *,
        expectation: PolicyExpectation | None,
    ) -> AuthoritativeExecutionPolicy:
        try:
            return self._policies.authorize(
                project_id=project_id,
                action_kind="CONNECTOR",
                expectation=expectation,
            )
        except PolicyDenied as exc:
            raise ConnectorFailure(
                ConnectorErrorCode(exc.receipt.denial_basis.value),
                str(exc),
                policy_denial=exc.receipt,
            ) from exc

    def _deny(
        self,
        *,
        project_id: str,
        basis: PolicyDenialBasis,
        message: str,
        authoritative: AuthoritativeExecutionPolicy,
        expectation: PolicyExpectation,
    ) -> None:
        try:
            self._policies.deny(
                project_id=project_id,
                action_kind="CONNECTOR",
                basis=basis,
                message=message,
                authoritative=authoritative,
                expectation=expectation,
            )
        except PolicyDenied as exc:
            raise ConnectorFailure(
                ConnectorErrorCode(exc.receipt.denial_basis.value),
                str(exc),
                policy_denial=exc.receipt,
            ) from exc

    def _preflight_request(
        self,
        request: ConnectorAccessRequest,
        policy: AuthoritativeExecutionPolicy,
    ) -> None:
        self._reject_raw_credentials(request.selector)

        if not policy.connector_allowlist:
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.POLICY_EMPTY_CONNECTOR_ALLOWLIST,
                message="authoritative project policy has an empty connector allowlist",
                authoritative=policy,
                expectation=policy.expectation,
            )
        if request.connector_id not in policy.connector_allowlist:
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.SCOPE_DENIED,
                message="connector is not allowed by authoritative project policy",
                authoritative=policy,
                expectation=policy.expectation,
            )
        if request.operation not in {ConnectorOperation.DISCOVER, ConnectorOperation.READ}:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "connector operation is not permitted in v1",
            )

    @staticmethod
    def _reject_raw_credentials(selector: dict[str, JsonValue]) -> None:
        forbidden_selector_keys = {
            "password",
            "secret",
            "token",
            "api_key",
            "access_key",
            "private_key",
            "dsn",
        }
        if forbidden_selector_keys.intersection(key.lower() for key in selector):
            raise ConnectorFailure(
                ConnectorErrorCode.AUTH_REQUIRED,
                "raw credentials are forbidden in connector selectors",
            )

    def _preflight_capability(
        self,
        request: ConnectorAccessRequest,
        capability: ConnectorCapability,
        policy: AuthoritativeExecutionPolicy,
    ) -> None:
        if request.operation not in capability.operations:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "operation is unsupported")
        if capability.write_supported:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "evidence connector cannot expose write capability",
            )
        if (
            capability.egress_class == "ALLOWLISTED_EXTERNAL"
            and (not policy.public_web_enabled or not policy.preferred_hosts)
            and request.connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID
        ):
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.SCOPE_DENIED,
                message="public web is off or has no preferred hosts",
                authoritative=policy,
                expectation=policy.expectation,
            )
        if request.connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID:
            setup = self._workspace_setup()
            if (
                setup.internet_consent != "ALLOWED"
                or not setup.internet_grant_id
                or setup.internet_grant_id != policy.workspace_internet_grant_id
            ):
                self._deny(
                    project_id=request.project_id,
                    basis=PolicyDenialBasis.SCOPE_DENIED,
                    message="workspace internet consent is absent, revoked, or stale",
                    authoritative=policy,
                    expectation=policy.expectation,
                )
        if capability.egress_class not in policy.connector_allowed_egress_classes:
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.EGRESS_DENIED,
                message="connector egress class is not allowed by authoritative project policy",
                authoritative=policy,
                expectation=policy.expectation,
            )
        if (
            capability.egress_class == "ALLOWLISTED_EXTERNAL"
            and request.query_security_class is not None
            and _SECURITY_ORDER[request.query_security_class]
            > _SECURITY_ORDER[policy.max_query_egress_security_class]
            and (
                request.selector.get("mode") == "SEARCH"
                or "query" in request.selector
                or request.connector_id != PROJECT_PUBLIC_WEB_CONNECTOR_ID
            )
        ):
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.EGRESS_DENIED,
                message="automatic query disclosure exceeds the project egress ceiling",
                authoritative=policy,
                expectation=policy.expectation,
            )
        if (
            _SECURITY_ORDER[request.security_class]
            > _SECURITY_ORDER[policy.max_source_security_class]
        ):
            self._deny(
                project_id=request.project_id,
                basis=PolicyDenialBasis.SECURITY_CLASS_DENIED,
                message="source security class exceeds authoritative project policy",
                authoritative=policy,
                expectation=policy.expectation,
            )

    def _receipt(
        self,
        *,
        request: ConnectorAccessRequest,
        capability: ConnectorCapability,
        run_id: str,
        fetched: ConnectorFetchResult,
    ) -> ConnectorReceipt:
        draft: dict[str, object] = {
            "connector_run_id": run_id,
            "project_id": request.project_id,
            "connector_id": request.connector_id,
            "driver_version": capability.driver_version,
            "policy_digest": request.policy_digest,
            "source_uri": fetched.ref.source_uri,
            "native_version": fetched.ref.native_version.model_dump(mode="json"),
            "content_sha256": fetched.content_sha256,
            "byte_size": len(fetched.raw),
            "recorded_at": self._clock.now(),
            "semantic_truth_certified": False,
        }
        return ConnectorReceipt.model_validate(
            {
                **draft,
                "receipt_digest": domain_digest(
                    "CONNECTOR_RECEIPT",
                    "1.0.0",
                    canonical_payload(draft),
                ),
            }
        )

    async def _close_after_failure(
        self, connector: ConnectorPort, run_id: str, request: ConnectorAccessRequest
    ) -> None:
        with suppress(Exception):
            await connector_cleanup(
                request, run_id, lambda: connector.close(run_id), self._controls
            )


def _catalog_selectors(
    connector_id: JsonValue,
    policy: AuthoritativeExecutionPolicy,
) -> list[JsonValue]:
    if connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID:
        return cast(list[JsonValue], list(registered_entrypoints(policy.preferred_hosts)))
    return [
        cast(JsonValue, route.selector)
        for route in (*policy.acquisition_routes, *policy.counter_search_routes)
        if route.connector_id == connector_id
    ]
