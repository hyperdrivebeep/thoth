from __future__ import annotations

from typing import Protocol

from thoth.domain.governance import (
    Organization,
    ProjectPolicy,
    ProjectReference,
    RoleAssignment,
    SourceBinding,
    Workstream,
)


class GovernanceStorePort(Protocol):
    def create_organization(self, value: Organization) -> None: ...

    def create_role(self, value: RoleAssignment) -> None: ...

    def list_roles(self, project_id: str) -> tuple[RoleAssignment, ...]: ...

    def revoke_role(
        self, project_id: str, role_assignment_id: str, *, revoked_at: str
    ) -> RoleAssignment: ...

    def create_workstream(self, value: Workstream) -> None: ...

    def list_workstreams(self, project_id: str) -> tuple[Workstream, ...]: ...

    def put_policy(self, value: ProjectPolicy) -> None: ...

    def read_policy(self, project_id: str) -> ProjectPolicy | None: ...

    def add_reference(self, value: ProjectReference) -> None: ...

    def list_references(self, project_id: str) -> tuple[ProjectReference, ...]: ...

    def put_source_binding(self, value: SourceBinding) -> None: ...

    def list_source_bindings(self, project_id: str) -> tuple[SourceBinding, ...]: ...

    def set_source_binding_state(
        self, project_id: str, binding_id: str, *, state: str, updated_at: str
    ) -> SourceBinding: ...


class ProjectPolicyReaderPort(Protocol):
    def read_policy(self, project_id: str) -> ProjectPolicy | None: ...
