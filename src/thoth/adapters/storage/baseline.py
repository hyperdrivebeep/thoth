from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

import orjson
from sqlalchemy import Engine, Table, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.elements import ColumnElement

from thoth.adapters.storage.schema import (
    baseline_candidates,
    baseline_decisions,
    baseline_sets,
    project_head_sets,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.base import DomainModel
from thoth.domain.baseline import (
    BaselineCandidate,
    BaselineDecision,
    BaselineSet,
    ProjectHeadSet,
)
from thoth.ports.baseline import BaselineStorePort

TRecord = TypeVar("TRecord", bound=DomainModel)


def _dump(value: DomainModel) -> str:
    return orjson.dumps(value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS).decode()


class SqliteBaselineStore(BaselineStorePort):
    def __init__(
        self,
        engine: Engine,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._fault_injector = fault_injector

    def put_head_set(self, value: ProjectHeadSet) -> None:
        payload = {
            "project_id": value.project_id,
            "revision": value.revision,
            "head_set_digest": value.head_set_digest,
            "content_json": _dump(value),
        }
        statement = sqlite_insert(project_head_sets).values(**payload)
        with write_connection(self._engine) as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["project_id"],
                    set_={key: child for key, child in payload.items() if key != "project_id"},
                )
            )

    def read_head_set(self, project_id: str) -> ProjectHeadSet | None:
        values = self._list(
            project_head_sets,
            project_head_sets.c.project_id == project_id,
            ProjectHeadSet,
        )
        return None if not values else values[-1]

    def add_candidate(self, value: BaselineCandidate) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(baseline_candidates).values(
                    candidate_id=value.candidate_id,
                    project_id=value.project_id,
                    scope=value.scope.value,
                    state=value.state,
                    candidate_digest=value.candidate_digest,
                    content_json=_dump(value),
                )
            )

    def read_candidate(self, project_id: str, candidate_id: str) -> BaselineCandidate | None:
        values = self._list(
            baseline_candidates,
            (baseline_candidates.c.project_id == project_id)
            & (baseline_candidates.c.candidate_id == candidate_id),
            BaselineCandidate,
        )
        return None if not values else values[-1]

    def list_candidates(self, project_id: str) -> tuple[BaselineCandidate, ...]:
        return self._list(
            baseline_candidates,
            baseline_candidates.c.project_id == project_id,
            BaselineCandidate,
        )

    def list_sets(self, project_id: str) -> tuple[BaselineSet, ...]:
        return self._list(
            baseline_sets,
            baseline_sets.c.project_id == project_id,
            BaselineSet,
        )

    def read_set_by_digest(self, project_id: str, digest: str) -> BaselineSet | None:
        values = self._list(
            baseline_sets,
            (baseline_sets.c.project_id == project_id)
            & (baseline_sets.c.baseline_set_digest == digest),
            BaselineSet,
        )
        return None if not values else values[-1]

    def mark_sets_stale(self, project_id: str, baseline_set_ids: tuple[str, ...]) -> None:
        if not baseline_set_ids:
            return
        selected = set(baseline_set_ids)
        with write_connection(self._engine) as connection:
            rows = connection.execute(
                select(baseline_sets.c.baseline_set_id, baseline_sets.c.content_json).where(
                    baseline_sets.c.project_id == project_id,
                    baseline_sets.c.baseline_set_id.in_(selected),
                )
            ).all()
            for row in rows:
                current = BaselineSet.model_validate(orjson.loads(str(row.content_json)))
                stale = current.model_copy(
                    update={"lifecycle": "STALE", "recalculation_required": True}
                )
                connection.execute(
                    baseline_sets.update()
                    .where(baseline_sets.c.baseline_set_id == current.baseline_set_id)
                    .values(lifecycle="STALE", content_json=_dump(stale))
                )

    def commit_decision(
        self,
        candidate: BaselineCandidate,
        decision: BaselineDecision,
        baseline: BaselineSet | None,
        superseded_ids: tuple[str, ...],
    ) -> None:
        with self._engine.begin() as connection:
            for baseline_set_id in superseded_ids:
                row = connection.execute(
                    select(baseline_sets.c.content_json).where(
                        baseline_sets.c.baseline_set_id == baseline_set_id
                    )
                ).first()
                if row is None:
                    continue
                current = BaselineSet.model_validate(orjson.loads(str(row.content_json)))
                superseded = current.model_copy(update={"lifecycle": "SUPERSEDED"})
                connection.execute(
                    baseline_sets.update()
                    .where(baseline_sets.c.baseline_set_id == baseline_set_id)
                    .values(lifecycle="SUPERSEDED", content_json=_dump(superseded))
                )
            revised_candidate = candidate.model_copy(
                update={"state": "APPROVED" if baseline is not None else "REJECTED"}
            )
            connection.execute(
                baseline_candidates.update()
                .where(baseline_candidates.c.candidate_id == candidate.candidate_id)
                .values(state=revised_candidate.state, content_json=_dump(revised_candidate))
            )
            if baseline is not None:
                connection.execute(
                    insert(baseline_sets).values(
                        baseline_set_id=baseline.baseline_set_id,
                        project_id=baseline.project_id,
                        scope=baseline.scope.value,
                        lifecycle=baseline.lifecycle,
                        baseline_set_digest=baseline.baseline_set_digest,
                        content_json=_dump(baseline),
                    )
                )
            if self._fault_injector is not None:
                self._fault_injector("after_baseline")
            connection.execute(
                insert(baseline_decisions).values(
                    decision_id=decision.decision_id,
                    project_id=decision.project_id,
                    candidate_id=decision.candidate_id,
                    decision=decision.decision,
                    decision_digest=decision.decision_digest,
                    content_json=_dump(decision),
                )
            )

    def _list(
        self,
        table: Table,
        condition: ColumnElement[bool],
        model: type[TRecord],
    ) -> tuple[TRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(select(table.c.content_json).where(condition)).all()
        return tuple(model.model_validate(orjson.loads(str(cast(object, row[0])))) for row in rows)
