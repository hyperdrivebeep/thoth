from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, insert, select, update

from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.adapters.storage.schema import (
    organizations,
    project_policies,
    project_references,
    project_roles,
    source_bindings,
    workstreams,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.governance import (
    Organization,
    ProjectPolicy,
    ProjectReference,
    RoleAssignment,
    SourceBinding,
    Workstream,
)
from thoth.ports.governance import GovernanceStorePort


def _list(value: str) -> tuple[str, ...]:
    loaded = cast(list[object], orjson.loads(value))
    return tuple(str(item) for item in loaded)


def _object(value: str) -> dict[str, object]:
    loaded = cast(object, orjson.loads(value))
    if not isinstance(loaded, dict):
        raise ValueError("stored governance payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], loaded).items()}


class SqliteGovernanceStore(GovernanceStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_organization(self, value: Organization) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(organizations).values(
                    organization_id=value.organization_id,
                    name=value.name,
                    kind=value.kind,
                    created_at=value.created_at.isoformat(),
                )
            )

    def create_role(self, value: RoleAssignment) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(project_roles).values(
                    role_assignment_id=value.role_assignment_id,
                    project_id=value.project_id,
                    actor_id=value.actor_id,
                    organization_id=value.organization_id,
                    role=value.role,
                    scope=value.scope,
                    authority_tags_json=orjson.dumps(value.authority_tags).decode(),
                    state=value.state,
                    created_at=value.created_at.isoformat(),
                    revoked_at=None,
                )
            )
            row = (
                connection.execute(
                    select(project_roles).where(
                        project_roles.c.role_assignment_id == value.role_assignment_id
                    )
                )
                .mappings()
                .one()
            )
            SqliteGovernanceHistory.stage(connection, "ROLE", dict(row))

    def list_roles(self, project_id: str) -> tuple[RoleAssignment, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(project_roles)
                    .where(project_roles.c.project_id == project_id)
                    .order_by(project_roles.c.created_at, project_roles.c.role_assignment_id)
                )
                .mappings()
                .all()
            )
            for row in rows:
                SqliteGovernanceHistory.assert_current(connection, "ROLE", dict(row))
            return tuple(self._role(row) for row in rows)

    def revoke_role(
        self, project_id: str, role_assignment_id: str, *, revoked_at: str
    ) -> RoleAssignment:
        with write_connection(self._engine) as connection:
            before = (
                connection.execute(
                    select(project_roles).where(
                        project_roles.c.project_id == project_id,
                        project_roles.c.role_assignment_id == role_assignment_id,
                    )
                )
                .mappings()
                .first()
            )
            changed = connection.execute(
                update(project_roles)
                .where(
                    project_roles.c.project_id == project_id,
                    project_roles.c.role_assignment_id == role_assignment_id,
                    project_roles.c.state == "ACTIVE",
                )
                .values(state="REVOKED", revoked_at=revoked_at)
            ).rowcount
            if changed != 1:
                raise KeyError(role_assignment_id)
            row = (
                connection.execute(
                    select(project_roles).where(
                        project_roles.c.role_assignment_id == role_assignment_id
                    )
                )
                .mappings()
                .one()
            )
            assert before is not None
            SqliteGovernanceHistory.stage(connection, "ROLE", dict(row), before=dict(before))
            return self._role(row)

    def create_workstream(self, value: Workstream) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(workstreams).values(
                    workstream_id=value.workstream_id,
                    project_id=value.project_id,
                    name=value.name,
                    parent_workstream_id=value.parent_workstream_id,
                    owner_role_assignment_id=value.owner_role_assignment_id,
                    state=value.state,
                    revision=value.revision,
                )
            )

    def list_workstreams(self, project_id: str) -> tuple[Workstream, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(workstreams)
                .where(workstreams.c.project_id == project_id)
                .order_by(workstreams.c.workstream_id)
            ).mappings()
            return tuple(
                Workstream(
                    workstream_id=str(row["workstream_id"]),
                    project_id=str(row["project_id"]),
                    name=str(row["name"]),
                    parent_workstream_id=(
                        None
                        if row["parent_workstream_id"] is None
                        else str(row["parent_workstream_id"])
                    ),
                    owner_role_assignment_id=(
                        None
                        if row["owner_role_assignment_id"] is None
                        else str(row["owner_role_assignment_id"])
                    ),
                    state=str(row["state"]),
                    revision=int(row["revision"]),
                )
                for row in rows
            )

    def put_policy(self, value: ProjectPolicy) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(project_policies).values(
                    policy_id=value.policy_id,
                    project_id=value.project_id,
                    version=value.version,
                    payload_json=orjson.dumps(value.payload, option=orjson.OPT_SORT_KEYS).decode(),
                    policy_digest=value.policy_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def read_policy(self, project_id: str) -> ProjectPolicy | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(project_policies)
                    .where(project_policies.c.project_id == project_id)
                    .order_by(project_policies.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return ProjectPolicy(
            policy_id=str(row["policy_id"]),
            project_id=str(row["project_id"]),
            version=int(row["version"]),
            payload=_object(str(row["payload_json"])),
            policy_digest=str(row["policy_digest"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def add_reference(self, value: ProjectReference) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(project_references).values(
                    reference_id=value.reference_id,
                    project_id=value.project_id,
                    origin_project_id=value.origin_project_id,
                    origin_revision=value.origin_revision,
                    rights_status=value.rights_status,
                    scope=value.scope,
                    authority_status=value.authority_status,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_references(self, project_id: str) -> tuple[ProjectReference, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(project_references)
                .where(project_references.c.project_id == project_id)
                .order_by(project_references.c.created_at)
            ).mappings()
            return tuple(
                ProjectReference(
                    reference_id=str(row["reference_id"]),
                    project_id=str(row["project_id"]),
                    origin_project_id=str(row["origin_project_id"]),
                    origin_revision=str(row["origin_revision"]),
                    rights_status=str(row["rights_status"]),
                    scope=str(row["scope"]),
                    authority_status=str(row["authority_status"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    def put_source_binding(self, value: SourceBinding) -> None:
        with write_connection(self._engine) as connection:
            existing = (
                connection.execute(
                    select(source_bindings).where(
                        source_bindings.c.project_id == value.project_id,
                        source_bindings.c.artifact_id == value.artifact_id,
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                connection.execute(
                    insert(source_bindings).values(
                        binding_id=value.binding_id,
                        project_id=value.project_id,
                        artifact_id=value.artifact_id,
                        capability=value.capability,
                        state=value.state,
                        created_at=value.created_at.isoformat(),
                        updated_at=value.updated_at.isoformat(),
                    )
                )
            else:
                connection.execute(
                    update(source_bindings)
                    .where(source_bindings.c.binding_id == str(existing["binding_id"]))
                    .values(
                        capability=value.capability,
                        state=value.state,
                        updated_at=value.updated_at.isoformat(),
                    )
                )
            identifier = value.binding_id if existing is None else str(existing["binding_id"])
            row = (
                connection.execute(
                    select(source_bindings).where(source_bindings.c.binding_id == identifier)
                )
                .mappings()
                .one()
            )
            SqliteGovernanceHistory.stage(
                connection,
                "SOURCE_BINDING",
                dict(row),
                before=None if existing is None else dict(existing),
            )

    def list_source_bindings(self, project_id: str) -> tuple[SourceBinding, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(source_bindings)
                    .where(source_bindings.c.project_id == project_id)
                    .order_by(source_bindings.c.created_at)
                )
                .mappings()
                .all()
            )
            for row in rows:
                SqliteGovernanceHistory.assert_current(connection, "SOURCE_BINDING", dict(row))
            return tuple(self._binding(row) for row in rows)

    def set_source_binding_state(
        self, project_id: str, binding_id: str, *, state: str, updated_at: str
    ) -> SourceBinding:
        with write_connection(self._engine) as connection:
            before = (
                connection.execute(
                    select(source_bindings).where(
                        source_bindings.c.project_id == project_id,
                        source_bindings.c.binding_id == binding_id,
                    )
                )
                .mappings()
                .first()
            )
            changed = connection.execute(
                update(source_bindings)
                .where(
                    source_bindings.c.project_id == project_id,
                    source_bindings.c.binding_id == binding_id,
                )
                .values(state=state, updated_at=updated_at)
            ).rowcount
            if changed != 1:
                raise KeyError(binding_id)
            row = (
                connection.execute(
                    select(source_bindings).where(source_bindings.c.binding_id == binding_id)
                )
                .mappings()
                .one()
            )
            assert before is not None
            SqliteGovernanceHistory.stage(
                connection, "SOURCE_BINDING", dict(row), before=dict(before)
            )
            return self._binding(row)

    @staticmethod
    def _role(row: object) -> RoleAssignment:
        values = cast(dict[str, object], row)
        return RoleAssignment(
            role_assignment_id=str(values["role_assignment_id"]),
            project_id=str(values["project_id"]),
            actor_id=str(values["actor_id"]),
            organization_id=(
                None if values["organization_id"] is None else str(values["organization_id"])
            ),
            role=str(values["role"]),
            scope=str(values["scope"]),
            authority_tags=_list(str(values["authority_tags_json"])),
            state=str(values["state"]),
            created_at=datetime.fromisoformat(str(values["created_at"])),
            revoked_at=(
                None
                if values["revoked_at"] is None
                else datetime.fromisoformat(str(values["revoked_at"]))
            ),
        )

    @staticmethod
    def _binding(row: object) -> SourceBinding:
        values = cast(dict[str, object], row)
        return SourceBinding(
            binding_id=str(values["binding_id"]),
            project_id=str(values["project_id"]),
            artifact_id=str(values["artifact_id"]),
            capability=str(values["capability"]),
            state=str(values["state"]),
            created_at=datetime.fromisoformat(str(values["created_at"])),
            updated_at=datetime.fromisoformat(str(values["updated_at"])),
        )
