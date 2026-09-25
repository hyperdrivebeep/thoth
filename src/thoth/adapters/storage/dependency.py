from __future__ import annotations

import orjson
from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import dependency_states, relations
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.enums import ImpactStatus
from thoth.domain.relation import DependencyRelation
from thoth.ports.dependency import DependencyGraphPort


class SqliteDependencyGraph(DependencyGraphPort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, relation: DependencyRelation) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(relations).values(
                    relation_id=relation.relation_id,
                    project_id=relation.project_id,
                    source_ref=relation.source_ref,
                    relation_type=relation.relation_type,
                    target_ref=relation.target_ref,
                    payload_json=orjson.dumps(
                        relation.payload, option=orjson.OPT_SORT_KEYS
                    ).decode(),
                    revision_digest=relation.revision_digest,
                )
            )

    def read_relations(self, project_id: str) -> tuple[DependencyRelation, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(select(relations).where(relations.c.project_id == project_id))
                .mappings()
                .all()
            )
        return tuple(
            DependencyRelation.model_validate(
                {
                    **{key: value for key, value in row.items() if key != "payload_json"},
                    "payload": orjson.loads(row["payload_json"]),
                }
            )
            for row in rows
        )

    def downstream(self, project_id: str, source_ref: str) -> tuple[str, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(relations.c.source_ref, relations.c.target_ref).where(
                    relations.c.project_id == project_id
                )
            ).all()
        adjacency: dict[str, list[str]] = {}
        for row in rows:
            adjacency.setdefault(str(row.source_ref), []).append(str(row.target_ref))
        visited: set[str] = set()
        frontier = list(adjacency.get(source_ref, ()))
        while frontier:
            target = frontier.pop(0)
            if target in visited or target == source_ref:
                continue
            visited.add(target)
            frontier.extend(adjacency.get(target, ()))
        return tuple(sorted(visited))

    def read_states(self, project_id: str) -> dict[str, ImpactStatus]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(dependency_states.c.entity_ref, dependency_states.c.status).where(
                    dependency_states.c.project_id == project_id
                )
            ).all()
        return {str(row.entity_ref): ImpactStatus(str(row.status)) for row in rows}

    def mark_current(
        self,
        project_id: str,
        entity_refs: tuple[str, ...],
        *,
        caused_by_revision: str,
        updated_at: str,
    ) -> None:
        with write_connection(self._engine) as connection:
            for entity_ref in entity_refs:
                statement = sqlite_insert(dependency_states).values(
                    project_id=project_id,
                    entity_ref=entity_ref,
                    status=ImpactStatus.CURRENT.value,
                    caused_by_revision=caused_by_revision,
                    updated_at=updated_at,
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["project_id", "entity_ref"],
                        set_={
                            "status": ImpactStatus.CURRENT.value,
                            "caused_by_revision": caused_by_revision,
                            "updated_at": updated_at,
                        },
                    )
                )
