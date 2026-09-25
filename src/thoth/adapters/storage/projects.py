from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.adapters.storage.schema import projects
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.project import Project
from thoth.ports.project import ProjectAlreadyExistsError, ProjectStorePort


class SqliteProjectStore(ProjectStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, project: Project, *, created_at: str) -> None:
        try:
            with write_connection(self._engine) as connection:
                connection.execute(
                    insert(projects).values(
                        project_id=project.project_id,
                        name=project.name,
                        description=project.description,
                        cutoff_at=project.cutoff_at.isoformat(),
                        lifecycle=project.lifecycle.value,
                        overlay=project.overlay,
                        policy_ref=project.policy_binding_ref,
                        created_at=created_at,
                        revision=project.revision,
                    )
                )
                row = (
                    connection.execute(
                        select(projects).where(projects.c.project_id == project.project_id)
                    )
                    .mappings()
                    .one()
                )
                SqliteGovernanceHistory.stage(connection, "PROJECT", dict(row))
        except IntegrityError as exc:
            raise ProjectAlreadyExistsError(project.project_id) from exc

    def read(self, project_id: str) -> Project | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(select(projects).where(projects.c.project_id == project_id))
                .mappings()
                .first()
            )
            if row is not None:
                SqliteGovernanceHistory.assert_current(connection, "PROJECT", dict(row))
        if row is None:
            return None
        return Project(
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            cutoff_at=datetime.fromisoformat(str(row["cutoff_at"])),
            lifecycle=ProjectLifecycle(str(row["lifecycle"])),
            overlay=str(row["overlay"]),
            policy_binding_ref=str(row["policy_ref"]),
            revision=int(row["revision"]),
        )

    def list(self) -> tuple[Project, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(select(projects).order_by(projects.c.created_at))
                .mappings()
                .all()
            )
            for row in rows:
                SqliteGovernanceHistory.assert_current(connection, "PROJECT", dict(row))
            return tuple(
                Project(
                    project_id=str(row["project_id"]),
                    name=str(row["name"]),
                    description=str(row["description"]),
                    cutoff_at=datetime.fromisoformat(str(row["cutoff_at"])),
                    lifecycle=ProjectLifecycle(str(row["lifecycle"])),
                    overlay=str(row["overlay"]),
                    policy_binding_ref=str(row["policy_ref"]),
                    revision=int(row["revision"]),
                )
                for row in rows
            )

    def update(self, project: Project, *, expected_revision: int) -> bool:
        with write_connection(self._engine) as connection:
            before = (
                connection.execute(
                    select(projects).where(projects.c.project_id == project.project_id)
                )
                .mappings()
                .first()
            )
            changed = connection.execute(
                update(projects)
                .where(
                    projects.c.project_id == project.project_id,
                    projects.c.revision == expected_revision,
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
            if changed == 1:
                assert before is not None
                after = (
                    connection.execute(
                        select(projects).where(projects.c.project_id == project.project_id)
                    )
                    .mappings()
                    .one()
                )
                SqliteGovernanceHistory.stage(
                    connection, "PROJECT", dict(after), before=dict(before)
                )
        return changed == 1
