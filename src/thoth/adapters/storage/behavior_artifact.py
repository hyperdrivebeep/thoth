from __future__ import annotations

from typing import cast

import orjson
from pydantic import BaseModel
from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import behavior_artifacts, behavior_registry
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.behavior_artifact import (
    BehaviorArtifact,
    BehaviorArtifactKind,
    BehaviorArtifactState,
    BehaviorRegistryEntry,
)
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort


def _dump(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


class SqliteBehaviorArtifactStore(BehaviorArtifactStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, value: BehaviorArtifact) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(behavior_artifacts).values(
                    artifact_id=value.artifact_id,
                    project_id=value.project_id,
                    kind=value.kind.value,
                    state=value.state.value,
                    content_digest=value.content_digest,
                    content_json=_dump(value),
                )
            )

    def read(self, project_id: str, artifact_id: str) -> BehaviorArtifact | None:
        with read_connection(self._engine) as connection:
            row = connection.execute(
                select(behavior_artifacts.c.content_json).where(
                    behavior_artifacts.c.project_id == project_id,
                    behavior_artifacts.c.artifact_id == artifact_id,
                )
            ).first()
        return None if row is None else BehaviorArtifact.model_validate(orjson.loads(str(row[0])))

    def list(
        self, project_id: str, kind: BehaviorArtifactKind | None = None
    ) -> tuple[BehaviorArtifact, ...]:
        statement = select(behavior_artifacts.c.content_json).where(
            behavior_artifacts.c.project_id == project_id
        )
        if kind is not None:
            statement = statement.where(behavior_artifacts.c.kind == kind.value)
        with read_connection(self._engine) as connection:
            rows = connection.execute(statement).all()
        return tuple(
            BehaviorArtifact.model_validate(orjson.loads(str(cast(object, row[0])))) for row in rows
        )

    def read_registry(
        self, project_id: str, kind: BehaviorArtifactKind
    ) -> BehaviorRegistryEntry | None:
        with read_connection(self._engine) as connection:
            row = connection.execute(
                select(behavior_registry.c.content_json).where(
                    behavior_registry.c.project_id == project_id,
                    behavior_registry.c.kind == kind.value,
                )
            ).first()
        return (
            None if row is None else BehaviorRegistryEntry.model_validate(orjson.loads(str(row[0])))
        )

    def activate(self, artifact: BehaviorArtifact, entry: BehaviorRegistryEntry) -> None:
        self._transition(artifact, entry, "ACTIVE")

    def rollback(self, candidate: BehaviorArtifact, entry: BehaviorRegistryEntry) -> None:
        self._transition(candidate, entry, "ROLLED_BACK")

    def _transition(
        self,
        artifact: BehaviorArtifact,
        entry: BehaviorRegistryEntry,
        state: str,
    ) -> None:
        with write_connection(self._engine) as connection:
            revised = artifact.model_copy(update={"state": BehaviorArtifactState(state)})
            connection.execute(
                behavior_artifacts.update()
                .where(behavior_artifacts.c.artifact_id == artifact.artifact_id)
                .values(state=state, content_json=_dump(revised))
            )
            payload = {
                "project_id": entry.project_id,
                "kind": entry.kind.value,
                "active_digest": entry.active_digest,
                "revision": entry.revision,
                "content_json": _dump(entry),
            }
            statement = sqlite_insert(behavior_registry).values(**payload)
            changed = connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["project_id", "kind"],
                    set_={
                        key: child
                        for key, child in payload.items()
                        if key not in {"project_id", "kind"}
                    },
                    where=behavior_registry.c.revision == entry.revision - 1,
                )
            ).rowcount
            if changed != 1:
                raise ValueError("behavior registry revision changed")
