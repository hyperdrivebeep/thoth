from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

import orjson

from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.ingestion_service import IngestArtifactCommand, IngestionService
from thoth.application.services.policy_gate import PolicyDenied, PolicyGate
from thoth.application.services.resource_scope_context import resource_stage_scope
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.ingestion import IngestionResult
from thoth.domain.policy import (
    AuthoritativeExecutionPolicy,
    PolicyDenialBasis,
    PolicyExpectation,
)
from thoth.domain.resource_scope import current_resource_uses
from thoth.domain.sandbox import (
    SandboxAdmissionBasis,
    SandboxErrorCode,
    SandboxFailure,
    SandboxInputSnapshot,
    SandboxReceipt,
    SandboxResult,
    SandboxRunSpec,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.sandbox import SandboxPort
from thoth.ports.sandbox_capability import SandboxCapabilityRouterPort


@dataclass(frozen=True)
class SandboxExecutionBundle:
    result: SandboxResult
    receipt: SandboxReceipt
    observation: IngestionResult
    basis: SandboxAdmissionBasis


class SandboxService:
    def __init__(
        self,
        *,
        router: SandboxCapabilityRouterPort,
        artifacts: ArtifactLedgerPort,
        objects: ObjectStorePort,
        controls: ControlRecordService,
        ingestion: IngestionService,
        projects: ProjectStorePort,
        policies: PolicyGate,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._router = router
        self._attempt_adapters: dict[str, SandboxPort] = {}
        self._artifacts = artifacts
        self._objects = objects
        self._controls = controls
        self._ingestion = ingestion
        self._projects = projects
        self._policies = policies
        self._ledger = ledger
        self._clock = clock
        self._ids = ids

    async def run(self, spec: SandboxRunSpec) -> SandboxExecutionBundle:
        self.preflight(spec)
        adapter, capability = self._router.resolve(spec.runtime_profile)
        effective = self._bind_inputs(spec)
        parent_refs = tuple(
            dict.fromkeys(
                (
                    *(item.artifact_id for item in effective.input_snapshots),
                    *(
                        use.resource_ref
                        for use in current_resource_uses() or ()
                        if use.project_id == spec.project_id and use.capability == "READ"
                    ),
                )
            )
        )
        project = self._projects.read(spec.project_id)
        if project is None:
            raise SandboxFailure(SandboxErrorCode.INPUT_INVALID, "sandbox project is unavailable")
        basis_draft: dict[str, object] = {
            "project_id": spec.project_id,
            "attempt_id": spec.attempt_id,
            "project_revision": project.revision,
            "cutoff_at": project.cutoff_at,
            "head_set_digest": head_set_digest(self._ledger.read_heads(spec.project_id)),
            "spec_digest": domain_digest(
                "SANDBOX_RUN_SPEC", "1.0.0", canonical_payload(spec.model_dump(mode="python"))
            ),
            "policy_id": spec.policy_id,
            "policy_revision": spec.policy_revision,
            "policy_digest": spec.policy_digest,
            "schema_version": "1.0.0",
        }
        basis = SandboxAdmissionBasis.model_validate(
            {
                **basis_draft,
                "basis_digest": domain_digest(
                    "SANDBOX_ADMISSION_BASIS", "1.0.0", canonical_payload(basis_draft)
                ),
            }
        )
        run_id = f"sandbox-run:{spec.attempt_id}"
        with self._ledger.transaction():
            self._validate_admission_basis(spec, basis)
            self._controls.create(
                project_id=spec.project_id,
                namespace="SANDBOX",
                record_type="RUN",
                state="RUNNING",
                payload={
                    "attempt_id": spec.attempt_id,
                    "admission_basis": basis.model_dump(mode="json"),
                    "runtime_profile": spec.runtime_profile.value,
                    "image_digest": spec.image_digest,
                    "policy_digest": spec.policy_digest,
                    "input_digests": [item.content_sha256 for item in effective.input_snapshots],
                },
                record_id=run_id,
            )
        self._attempt_adapters[spec.attempt_id] = adapter
        try:
            result = await adapter.run(effective)
        finally:
            if self._attempt_adapters.get(spec.attempt_id) is adapter:
                self._attempt_adapters.pop(spec.attempt_id, None)
        if (
            result.project_id != spec.project_id
            or result.attempt_id != spec.attempt_id
            or result.runtime_profile != spec.runtime_profile
        ):
            raise SandboxFailure(SandboxErrorCode.INPUT_INVALID, "sandbox result identity mismatch")
        receipt_draft: dict[str, object] = {
            "project_id": spec.project_id,
            "attempt_id": spec.attempt_id,
            "runtime_profile": spec.runtime_profile.value,
            "image_digest": spec.image_digest,
            "policy_digest": spec.policy_digest,
            "input_digests": tuple(item.content_sha256 for item in effective.input_snapshots),
            "output_digests": result.output_digests,
            "result_state": result.state.value,
            "exit_code": result.exit_code,
            "cleanup_state": result.cleanup_state.value,
            "runtime_version": capability.runtime_version,
            "security_tier": capability.security_tier,
            "recorded_at": self._clock.now(),
            "semantic_truth_certified": False,
        }
        receipt = SandboxReceipt.model_validate(
            {
                **receipt_draft,
                "receipt_digest": domain_digest(
                    "SANDBOX_RECEIPT", "1.0.0", canonical_payload(receipt_draft)
                ),
            }
        )
        self._capture_result(result, receipt, basis, parent_refs)
        observation_raw = orjson.dumps(
            {
                "attempt_id": spec.attempt_id,
                "runtime_profile": spec.runtime_profile.value,
                "state": result.state.value,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output_digests": result.output_digests,
                "cleanup_state": result.cleanup_state.value,
                "failure_detail": result.failure_detail,
                "sandbox_receipt_digest": receipt.receipt_digest,
                "semantic_truth_certified": False,
            },
            option=orjson.OPT_SORT_KEYS,
        )
        observation = self._ingestion.stage(
            IngestArtifactCommand(
                project_id=spec.project_id,
                source_uri=f"sandbox://{spec.attempt_id}/result",
                media_type="application/json",
                raw=observation_raw,
                authority=AuthorityState.INFORMAL,
                cutoff_state=CutoffState.PROHIBITED_CONTEXT,
                security_class=self._result_security_class(spec),
                operation_id=self._ids.new("sandbox-observation"),
                version_label=receipt.receipt_digest,
            ),
            derived_parents=parent_refs,
        )
        with self._ledger.transaction():
            self._controls.create(
                project_id=spec.project_id,
                namespace="SANDBOX",
                record_type="RUN",
                state=result.state.value,
                payload={
                    **result.model_dump(mode="json"),
                    "receipt_digest": receipt.receipt_digest,
                    "admission_basis": basis.model_dump(mode="json"),
                    "evidence_admission": "PENDING_ACCEPTANCE",
                    "resource_parent_refs": parent_refs,
                    "observation_artifact_id": observation.document.artifact.artifact_id,
                    "observation_content_digest": observation.object_digest,
                },
                record_id=run_id,
            )
            with resource_stage_scope(observation.resource_scope):
                self._artifacts.persist_ingestion(
                    observation.document,
                    observation.source_version_id,
                    observation.evidence_candidates,
                )
        return SandboxExecutionBundle(
            result=result,
            receipt=receipt,
            observation=observation,
            basis=basis,
        )

    def _capture_result(
        self,
        result: SandboxResult,
        receipt: SandboxReceipt,
        basis: SandboxAdmissionBasis,
        parent_refs: tuple[str, ...],
    ) -> None:
        # The already executed process cannot be rolled back when source access changes.
        # Capture the fact separately; scientific admission still requires current access.
        with self._ledger.transaction():
            self._controls.create(
                project_id=result.project_id,
                namespace="SANDBOX",
                record_type="RUN",
                record_id=f"sandbox-run:{result.attempt_id}",
                state=result.state.value,
                payload={
                    **result.model_dump(mode="json"),
                    "receipt_digest": receipt.receipt_digest,
                    "admission_basis": basis.model_dump(mode="json"),
                    "resource_parent_refs": parent_refs,
                    "evidence_admission": "CAPTURED_NOT_ADMITTED",
                },
            )
            self._controls.create(
                project_id=result.project_id,
                namespace="SANDBOX",
                record_type="RECEIPT",
                record_id=f"sandbox-receipt:{result.attempt_id}",
                state="SEALED",
                payload=receipt.model_dump(mode="json"),
            )

    def _validate_admission_basis(
        self,
        spec: SandboxRunSpec,
        basis: SandboxAdmissionBasis,
    ) -> None:
        try:
            SandboxAdmissionBasis.model_validate(basis.model_dump(mode="python"))
        except ValueError as exc:
            raise SandboxFailure(
                SandboxErrorCode.INPUT_INVALID, "sandbox basis integrity failed"
            ) from exc
        spec_digest = domain_digest(
            "SANDBOX_RUN_SPEC", "1.0.0", canonical_payload(spec.model_dump(mode="python"))
        )
        if (
            basis.project_id != spec.project_id
            or basis.attempt_id != spec.attempt_id
            or basis.spec_digest != spec_digest
            or basis.policy_id != spec.policy_id
            or basis.policy_revision != spec.policy_revision
            or basis.policy_digest != spec.policy_digest
        ):
            raise SandboxFailure(
                SandboxErrorCode.INPUT_INVALID, "sandbox basis does not match run spec"
            )
        expectation = PolicyExpectation(
            policy_id=basis.policy_id,
            policy_revision=basis.policy_revision,
            policy_digest=basis.policy_digest,
        )
        self._authorize(spec.project_id, expectation)
        project = self._projects.read(spec.project_id)
        if (
            project is None
            or project.revision != basis.project_revision
            or project.cutoff_at != basis.cutoff_at
            or project.policy_binding_ref != basis.policy_id
            or (
                spec.project_revision is not None
                and spec.project_revision != basis.project_revision
            )
            or (spec.cutoff_at is not None and spec.cutoff_at != basis.cutoff_at)
            or (
                spec.current_head_set_digest is not None
                and spec.current_head_set_digest != basis.head_set_digest
            )
            or head_set_digest(self._ledger.read_heads(spec.project_id)) != basis.head_set_digest
        ):
            raise SandboxFailure(
                SandboxErrorCode.INPUT_INVALID, "sandbox project/cutoff/HeadSet changed"
            )

    @contextmanager
    def accepting_observation(
        self,
        spec: SandboxRunSpec,
        bundle: SandboxExecutionBundle,
    ) -> Generator[ArtifactEnvelope, None, None]:
        try:
            with self._ledger.transaction():
                self._validate_admission_basis(spec, bundle.basis)
                artifact = self._artifacts.read_artifact(
                    bundle.observation.document.artifact.artifact_id
                )
                if (
                    bundle.result.project_id != spec.project_id
                    or bundle.result.attempt_id != spec.attempt_id
                    or bundle.receipt.project_id != spec.project_id
                    or bundle.receipt.attempt_id != spec.attempt_id
                    or artifact != bundle.observation.document.artifact
                    or artifact is None
                    or artifact.project_id != spec.project_id
                    or artifact.source_uri != f"sandbox://{spec.attempt_id}/result"
                    or artifact.byte_sha256 != bundle.observation.object_digest
                ):
                    raise SandboxFailure(
                        SandboxErrorCode.INPUT_INVALID, "sandbox artifact binding failed"
                    )
                for expected in bundle.observation.evidence_candidates:
                    current = self._artifacts.read_evidence(expected.span_id)
                    if (
                        current is None
                        or current != expected
                        or current.project_id != spec.project_id
                        or current.artifact_id != artifact.artifact_id
                    ):
                        raise SandboxFailure(
                            SandboxErrorCode.INPUT_INVALID,
                            "sandbox observation changed before admission",
                        )
                    self._artifacts.update_evidence(
                        current.model_copy(update={"cutoff_state": CutoffState.ELIGIBLE})
                    )
                self._artifacts.update_artifact_cutoff(artifact, CutoffState.ELIGIBLE)
                # Only the caller's short deterministic acceptance writes belong here.
                yield artifact.model_copy(update={"cutoff_state": CutoffState.ELIGIBLE})
                self._controls.create(
                    project_id=spec.project_id,
                    namespace="SANDBOX",
                    record_type="ADMISSION",
                    record_id=f"sandbox-admission:{spec.attempt_id}",
                    state="ACCEPTED",
                    payload={
                        "basis": bundle.basis.model_dump(mode="json"),
                        "sandbox_receipt_digest": bundle.receipt.receipt_digest,
                        "observation_refs": tuple(
                            span.span_id for span in bundle.observation.evidence_candidates
                        ),
                        "accepted_head_set_digest": head_set_digest(
                            self._ledger.read_heads(spec.project_id)
                        ),
                        "semantic_truth_certified": False,
                    },
                )
        except SandboxFailure:
            self.reject_observation(bundle, reason="SANDBOX_ADMISSION_BASIS_CHANGED")
            raise

    def reject_observation(self, bundle: SandboxExecutionBundle, *, reason: str) -> None:
        with self._ledger.transaction():
            artifact = self._artifacts.read_artifact(
                bundle.observation.document.artifact.artifact_id
            )
            if artifact is not None and artifact.project_id == bundle.result.project_id:
                self._artifacts.update_artifact_cutoff(artifact, CutoffState.PROHIBITED_CONTEXT)
            for expected in bundle.observation.evidence_candidates:
                current = self._artifacts.read_evidence(expected.span_id)
                if current is not None and current.project_id == bundle.result.project_id:
                    self._artifacts.update_evidence(
                        current.model_copy(update={"cutoff_state": CutoffState.PROHIBITED_CONTEXT})
                    )
            self._controls.create(
                project_id=bundle.result.project_id,
                namespace="SANDBOX",
                record_type="ADMISSION",
                record_id=f"sandbox-admission:{bundle.result.attempt_id}",
                state="HELD",
                payload={
                    "basis": bundle.basis.model_dump(mode="json"),
                    "reason_code": reason,
                    "sandbox_receipt_digest": bundle.receipt.receipt_digest,
                    "observation_refs": tuple(
                        span.span_id for span in bundle.observation.evidence_candidates
                    ),
                    "semantic_truth_certified": False,
                },
            )

    def preflight(self, spec: SandboxRunSpec) -> None:
        expectation = PolicyExpectation(
            policy_id=spec.policy_id,
            policy_revision=spec.policy_revision,
            policy_digest=spec.policy_digest,
        )
        policy = self._authorize(spec.project_id, expectation)
        project = self._projects.read(spec.project_id)
        if project is None:
            raise SandboxFailure(SandboxErrorCode.INPUT_INVALID, "sandbox project does not exist")
        if (spec.project_revision is not None and spec.project_revision != project.revision) or (
            spec.cutoff_at is not None and spec.cutoff_at != project.cutoff_at
        ):
            raise SandboxFailure(SandboxErrorCode.INPUT_INVALID, "sandbox project/cutoff is stale")
        if project.policy_binding_ref != policy.policy_id:
            self._deny(
                project_id=spec.project_id,
                basis=PolicyDenialBasis.POLICY_BINDING_MISMATCH,
                message="project is not bound to the authoritative sandbox policy",
                authoritative=policy,
                expectation=expectation,
            )
        if spec.current_head_set_digest is not None:
            current_head = head_set_digest(self._ledger.read_heads(spec.project_id))
            if current_head != spec.current_head_set_digest:
                raise SandboxFailure(
                    SandboxErrorCode.INPUT_INVALID,
                    "sandbox current HeadSet digest is stale",
                )
        if spec.runtime_profile.value not in policy.sandbox_runtime_allowlist:
            self._deny(
                project_id=spec.project_id,
                basis=PolicyDenialBasis.SANDBOX_RUNTIME_DENIED,
                message="sandbox runtime is not allowed by authoritative project policy",
                authoritative=policy,
                expectation=expectation,
            )
        if policy.sandbox_network_policy == "DENY_ALL":
            if spec.network_policy.value != "DENY_ALL" or spec.allowed_hosts:
                self._deny(
                    project_id=spec.project_id,
                    basis=PolicyDenialBasis.EGRESS_DENIED,
                    message="sandbox egress is denied by authoritative project policy",
                    authoritative=policy,
                    expectation=expectation,
                )
        elif spec.network_policy.value == "ALLOWLIST" and not set(spec.allowed_hosts).issubset(
            policy.sandbox_allowed_hosts
        ):
            self._deny(
                project_id=spec.project_id,
                basis=PolicyDenialBasis.EGRESS_DENIED,
                message="sandbox host is outside the authoritative egress allowlist",
                authoritative=policy,
                expectation=expectation,
            )
        security_order = {
            SecurityClass.PUBLIC: 0,
            SecurityClass.INTERNAL: 1,
            SecurityClass.CONFIDENTIAL: 2,
            SecurityClass.RESTRICTED: 3,
        }
        for snapshot in spec.input_snapshots:
            artifact = self._artifacts.read_artifact(snapshot.artifact_id)
            if artifact is None or artifact.project_id != spec.project_id:
                raise SandboxFailure(
                    SandboxErrorCode.INPUT_INVALID,
                    "sandbox input artifact is outside the project",
                )
            if artifact.cutoff_state != CutoffState.ELIGIBLE:
                raise SandboxFailure(
                    SandboxErrorCode.INPUT_INVALID, "sandbox input artifact is ineligible"
                )
            if artifact.byte_sha256 != snapshot.content_sha256:
                raise SandboxFailure(SandboxErrorCode.INPUT_INVALID, "sandbox input digest changed")
            if (
                security_order[artifact.security_class]
                > security_order[policy.max_source_security_class]
            ):
                self._deny(
                    project_id=spec.project_id,
                    basis=PolicyDenialBasis.SECURITY_CLASS_DENIED,
                    message="sandbox input security class exceeds authoritative project policy",
                    authoritative=policy,
                    expectation=expectation,
                )

    async def cancel(self, attempt_id: str) -> bool:
        adapter = self._attempt_adapters.get(attempt_id)
        return False if adapter is None else await adapter.cancel(attempt_id)

    def _authorize(
        self,
        project_id: str,
        expectation: PolicyExpectation,
    ) -> AuthoritativeExecutionPolicy:
        try:
            return self._policies.authorize(
                project_id=project_id,
                action_kind="SANDBOX",
                expectation=expectation,
            )
        except PolicyDenied as exc:
            raise SandboxFailure(
                SandboxErrorCode(exc.receipt.denial_basis.value),
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
                action_kind="SANDBOX",
                basis=basis,
                message=message,
                authoritative=authoritative,
                expectation=expectation,
            )
        except PolicyDenied as exc:
            raise SandboxFailure(
                SandboxErrorCode(exc.receipt.denial_basis.value),
                str(exc),
                policy_denial=exc.receipt,
            ) from exc

    def _bind_inputs(self, spec: SandboxRunSpec) -> SandboxRunSpec:
        bound: list[SandboxInputSnapshot] = []
        for snapshot in spec.input_snapshots:
            artifact = self._artifacts.read_artifact(snapshot.artifact_id)
            if artifact is None or artifact.project_id != spec.project_id:
                raise ValueError("sandbox input artifact is outside the project")
            if artifact.cutoff_state != CutoffState.ELIGIBLE:
                raise SandboxFailure(
                    SandboxErrorCode.INPUT_INVALID, "sandbox input artifact is ineligible"
                )
            if artifact.byte_sha256 != snapshot.content_sha256:
                raise ValueError("sandbox input digest does not match artifact ledger")
            path = self._objects.path_for(snapshot.content_sha256)
            self._objects.read(snapshot.content_sha256)
            bound.append(snapshot.model_copy(update={"source_path": str(path)}))
        return spec.model_copy(update={"input_snapshots": tuple(bound)})

    def _result_security_class(self, spec: SandboxRunSpec) -> SecurityClass:
        order = {
            SecurityClass.PUBLIC: 0,
            SecurityClass.INTERNAL: 1,
            SecurityClass.CONFIDENTIAL: 2,
            SecurityClass.RESTRICTED: 3,
        }
        values = [SecurityClass.INTERNAL]
        for snapshot in spec.input_snapshots:
            artifact = self._artifacts.read_artifact(snapshot.artifact_id)
            if artifact is not None:
                values.append(artifact.security_class)
        return max(values, key=order.__getitem__)
