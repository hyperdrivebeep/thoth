from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from thoth.domain.base import DomainModel


class DeploymentKind(StrEnum):
    LOCAL = "LOCAL"
    INTRANET = "INTRANET"
    PRIVATE_CLOUD = "PRIVATE_CLOUD"


class ModelRoute(StrEnum):
    CODEX_OAUTH = "CODEX_OAUTH"
    OPENAI_RESPONSES = "OPENAI_RESPONSES"
    OPENAI_COMPATIBLE_LOCAL = "OPENAI_COMPATIBLE_LOCAL"


class EgressPolicy(StrEnum):
    DENY_ALL = "DENY_ALL"
    MODEL_ONLY = "MODEL_ONLY"
    ALLOWLIST = "ALLOWLIST"


class SandboxRoute(StrEnum):
    DISABLED = "DISABLED"
    DOCKER_POC = "DOCKER_POC"
    GVISOR = "GVISOR"
    FIRECRACKER = "FIRECRACKER"
    MANAGED = "MANAGED"


class EnvironmentProfile(DomainModel):
    profile_id: str
    deployment: DeploymentKind
    bind_host: str
    model_route: ModelRoute
    egress_policy: EgressPolicy
    allowed_egress_hosts: tuple[str, ...] = ()
    connector_allowlist: tuple[str, ...]
    data_residency: str
    model_data_may_leave_boundary: bool
    external_writes_enabled: bool = False
    physical_actions_enabled: bool = False
    adapter_available: bool
    sandbox_route: SandboxRoute = SandboxRoute.DISABLED
    sandbox_adapter_available: bool = False
    sandbox_network_policy: str = "DENY_ALL"
    notes: tuple[str, ...] = ()
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_boundary(self) -> EnvironmentProfile:
        if (
            self.bind_host not in {"127.0.0.1", "localhost", "::1"}
            and self.deployment == DeploymentKind.LOCAL
        ):
            raise ValueError("LOCAL profile must bind to loopback")
        if self.egress_policy == EgressPolicy.DENY_ALL and self.allowed_egress_hosts:
            raise ValueError("DENY_ALL cannot include egress hosts")
        if self.egress_policy == EgressPolicy.ALLOWLIST and not self.allowed_egress_hosts:
            raise ValueError("ALLOWLIST requires at least one host")
        if self.model_route == ModelRoute.CODEX_OAUTH:
            if self.egress_policy != EgressPolicy.MODEL_ONLY:
                raise ValueError("Codex OAuth requires MODEL_ONLY egress")
            if not self.model_data_may_leave_boundary:
                raise ValueError("hosted Codex model requires explicit data-egress acknowledgement")
        if self.deployment == DeploymentKind.INTRANET and self.model_data_may_leave_boundary:
            raise ValueError("INTRANET profile cannot permit model data to leave the boundary")
        if self.external_writes_enabled or self.physical_actions_enabled:
            raise ValueError("current THOTH profiles cannot enable external or physical actions")
        if self.deployment == DeploymentKind.LOCAL and self.sandbox_route in {
            SandboxRoute.GVISOR,
            SandboxRoute.FIRECRACKER,
        }:
            raise ValueError("LOCAL Windows profile cannot claim gVisor or Firecracker runtime")
        if self.sandbox_network_policy != "DENY_ALL":
            raise ValueError("current THOTH sandbox profiles require DENY_ALL")
        return self
