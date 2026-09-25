from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.policy import PolicyDenialReceipt


class SandboxErrorCode(StrEnum):
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_CONFIGURATION_INVALID = "POLICY_CONFIGURATION_INVALID"
    POLICY_BINDING_MISMATCH = "POLICY_BINDING_MISMATCH"
    POLICY_DIGEST_MISMATCH = "POLICY_DIGEST_MISMATCH"
    SECURITY_CLASS_DENIED = "SECURITY_CLASS_DENIED"
    EGRESS_DENIED = "EGRESS_DENIED"
    SANDBOX_RUNTIME_DENIED = "SANDBOX_RUNTIME_DENIED"
    INPUT_INVALID = "INPUT_INVALID"


class SandboxRuntimeProfile(StrEnum):
    SCRIPTED = "SCRIPTED"
    DOCKER_POC = "DOCKER_POC"
    GVISOR = "GVISOR"
    FIRECRACKER = "FIRECRACKER"
    MANAGED = "MANAGED"


class SandboxNetworkPolicy(StrEnum):
    DENY_ALL = "DENY_ALL"
    ALLOWLIST = "ALLOWLIST"


class SandboxExecutionState(StrEnum):
    CREATED = "CREATED"
    STAGED = "STAGED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    OOM_KILLED = "OOM_KILLED"
    CANCELLED = "CANCELLED"
    EGRESS_BLOCKED = "EGRESS_BLOCKED"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    POLICY_DENIED = "POLICY_DENIED"
    BOOT_FAILED = "BOOT_FAILED"
    DESTROYED = "DESTROYED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class SandboxResourceLimits(DomainModel):
    cpu_millis: int = Field(default=1_000, ge=100, le=16_000)
    memory_mib: int = Field(default=512, ge=64, le=32_768)
    pids: int = Field(default=128, ge=8, le=4_096)
    disk_mib: int = Field(default=512, ge=16, le=16_384)
    wall_seconds: int = Field(default=60, ge=1, le=3_600)
    stdout_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    stderr_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)


class SandboxInputSnapshot(DomainModel):
    artifact_id: str = Field(min_length=1, max_length=160)
    content_sha256: Sha256
    source_path: str = Field(min_length=1, max_length=1_000)
    target_name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")


class SandboxRunSpec(DomainModel):
    project_id: ProjectId
    attempt_id: str = Field(min_length=1, max_length=160)
    runtime_profile: SandboxRuntimeProfile
    image_digest: str = Field(min_length=1, max_length=500)
    argv: tuple[str, ...]
    input_snapshots: tuple[SandboxInputSnapshot, ...] = ()
    network_policy: SandboxNetworkPolicy = SandboxNetworkPolicy.DENY_ALL
    allowed_hosts: tuple[str, ...] = ()
    resource_limits: SandboxResourceLimits = SandboxResourceLimits()
    secret_refs: tuple[str, ...] = ()
    policy_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    working_directory: str = "/work"
    action_id: str | None = Field(default=None, max_length=160)
    action_plan_id: str | None = Field(default=None, max_length=160)
    current_head_set_digest: Sha256 | None = None
    project_revision: int | None = Field(default=None, ge=0)
    cutoff_at: AwareDatetime | None = None
    input_context_digest: Sha256 | None = None

    @model_validator(mode="after")
    def enforce_safe_contract(self) -> SandboxRunSpec:
        if not self.argv or any(not item for item in self.argv):
            raise ValueError("sandbox argv must be a non-empty argument array")
        if self.network_policy == SandboxNetworkPolicy.DENY_ALL and self.allowed_hosts:
            raise ValueError("DENY_ALL cannot include allowed hosts")
        if self.network_policy == SandboxNetworkPolicy.ALLOWLIST and not self.allowed_hosts:
            raise ValueError("ALLOWLIST requires hosts")
        if self.secret_refs:
            raise ValueError("Sandbox v1 does not inject secrets")
        if (
            self.runtime_profile
            in {
                SandboxRuntimeProfile.DOCKER_POC,
                SandboxRuntimeProfile.GVISOR,
            }
            and re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", self.image_digest)
            is None
        ):
            raise ValueError("OCI sandbox image must be a strict pinned digest reference")
        if (
            self.runtime_profile == SandboxRuntimeProfile.DOCKER_POC
            and self.network_policy != SandboxNetworkPolicy.DENY_ALL
        ):
            raise ValueError("Docker POC only supports DENY_ALL")
        return self


class SandboxAdmissionBasis(DomainModel):
    project_id: ProjectId
    attempt_id: str
    project_revision: int = Field(ge=0)
    cutoff_at: AwareDatetime
    head_set_digest: Sha256
    spec_digest: Sha256
    policy_id: str
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    basis_digest: Sha256
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def verify_basis_digest(self) -> SandboxAdmissionBasis:
        expected = domain_digest(
            "SANDBOX_ADMISSION_BASIS",
            "1.0.0",
            canonical_payload(self.model_dump(mode="python", exclude={"basis_digest"})),
        )
        if self.basis_digest != expected:
            raise ValueError("sandbox admission basis digest mismatch")
        return self


class SandboxResult(DomainModel):
    project_id: ProjectId
    attempt_id: str
    runtime_profile: SandboxRuntimeProfile
    state: SandboxExecutionState
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_digests: tuple[Sha256, ...] = ()
    started_at: AwareDatetime
    completed_at: AwareDatetime
    cleanup_state: SandboxExecutionState
    runtime_version: str = "UNRECORDED"
    security_tier: str = "UNRECORDED"
    failure_detail: str | None = None


class SandboxReceipt(DomainModel):
    project_id: ProjectId
    attempt_id: str
    runtime_profile: SandboxRuntimeProfile
    image_digest: str
    policy_digest: Sha256
    input_digests: tuple[Sha256, ...]
    output_digests: tuple[Sha256, ...]
    result_state: SandboxExecutionState
    exit_code: int | None = None
    cleanup_state: SandboxExecutionState
    runtime_version: str = "UNRECORDED"
    security_tier: str = "UNRECORDED"
    recorded_at: AwareDatetime
    receipt_digest: Sha256
    semantic_truth_certified: Literal[False] = False


class SandboxFailure(RuntimeError):
    def __init__(
        self,
        code: SandboxErrorCode,
        message: str,
        *,
        policy_denial: PolicyDenialReceipt | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.policy_denial = policy_denial
