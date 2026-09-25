from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, Integer, Table, insert, literal_column, select

from thoth.adapters.storage.schema import (
    execution_audit,
    execution_effects,
    execution_reconciliations,
    plan_executions,
    step_execution_attempts,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.base import DomainModel
from thoth.domain.execution_full import (
    ExecutionAuditRecord,
    ExecutionEffectRecord,
    PlanExecutionRecord,
    ReconciliationRecord,
    StepExecutionAttemptRecord,
)
from thoth.ports.execution import ExecutionStorePort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored execution payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteExecutionStore(ExecutionStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _add(self, table: Table, value: DomainModel, **columns: object) -> None:
        created_at = (
            value.updated_at
            if isinstance(value, PlanExecutionRecord)
            else getattr(value, "created_at", None)
        )
        if not isinstance(created_at, datetime):
            raise TypeError("stored execution record requires created_at")
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(table).values(
                    **columns,
                    content_json=dump(value.model_dump(mode="json")),
                    created_at=created_at.isoformat(),
                )
            )

    def add_execution(self, value: PlanExecutionRecord) -> None:
        self._add(
            plan_executions,
            value,
            execution_revision_id=value.execution_revision_id,
            plan_execution_id=value.plan_execution_id,
            project_id=value.project_id,
            object_id=value.object_id,
            plan_id=value.plan_id,
            revision_digest=value.revision_digest,
        )

    def list_executions(self, project_id: str) -> tuple[PlanExecutionRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(plan_executions)
                .where(plan_executions.c.project_id == project_id)
                .order_by(plan_executions.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            values = tuple(
                PlanExecutionRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, PlanExecutionRecord] = {}
        for value in values:
            latest[value.plan_execution_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_execution(self, project_id: str, plan_execution_id: str) -> PlanExecutionRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(plan_executions)
                    .where(
                        plan_executions.c.project_id == project_id,
                        plan_executions.c.plan_execution_id == plan_execution_id,
                    )
                    .order_by(
                        plan_executions.c.created_at.desc(),
                        literal_column("rowid", Integer()).desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return (
            None if row is None else PlanExecutionRecord.model_validate(load(row["content_json"]))
        )

    def add_attempt(self, value: StepExecutionAttemptRecord) -> None:
        self._add(
            step_execution_attempts,
            value,
            attempt_revision_id=value.attempt_revision_id,
            attempt_id=value.attempt_id,
            project_id=value.project_id,
            plan_execution_id=value.plan_execution_id,
            step_id=value.step_id,
            revision_digest=value.revision_digest,
        )

    def list_attempts(
        self, project_id: str, plan_execution_id: str
    ) -> tuple[StepExecutionAttemptRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(step_execution_attempts)
                .where(
                    step_execution_attempts.c.project_id == project_id,
                    step_execution_attempts.c.plan_execution_id == plan_execution_id,
                )
                .order_by(step_execution_attempts.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            values = tuple(
                StepExecutionAttemptRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, StepExecutionAttemptRecord] = {}
        for value in values:
            latest[value.attempt_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_attempt(self, project_id: str, attempt_id: str) -> StepExecutionAttemptRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(step_execution_attempts)
                    .where(
                        step_execution_attempts.c.project_id == project_id,
                        step_execution_attempts.c.attempt_id == attempt_id,
                    )
                    .order_by(
                        step_execution_attempts.c.created_at.desc(),
                        literal_column("rowid", Integer()).desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return (
            None
            if row is None
            else StepExecutionAttemptRecord.model_validate(load(row["content_json"]))
        )

    def add_effect(self, value: ExecutionEffectRecord) -> None:
        self._add(
            execution_effects,
            value,
            effect_id=value.effect_id,
            project_id=value.project_id,
            attempt_id=value.attempt_id,
            effect_digest=value.effect_digest,
        )

    def list_effects(self, project_id: str, attempt_id: str) -> tuple[ExecutionEffectRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(execution_effects)
                .where(
                    execution_effects.c.project_id == project_id,
                    execution_effects.c.attempt_id == attempt_id,
                )
                .order_by(execution_effects.c.created_at)
            ).mappings()
            return tuple(
                ExecutionEffectRecord.model_validate(load(row["content_json"])) for row in rows
            )

    def add_reconciliation(self, value: ReconciliationRecord) -> None:
        self._add(
            execution_reconciliations,
            value,
            reconciliation_id=value.reconciliation_id,
            project_id=value.project_id,
            plan_execution_id=value.plan_execution_id,
            attempt_id=value.attempt_id,
            reconciliation_digest=value.reconciliation_digest,
        )

    def read_reconciliation(
        self, project_id: str, reconciliation_id: str
    ) -> ReconciliationRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(execution_reconciliations).where(
                        execution_reconciliations.c.project_id == project_id,
                        execution_reconciliations.c.reconciliation_id == reconciliation_id,
                    )
                )
                .mappings()
                .first()
            )
        return (
            None if row is None else ReconciliationRecord.model_validate(load(row["content_json"]))
        )

    def append_audit(self, value: ExecutionAuditRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(execution_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    plan_execution_id=value.plan_execution_id,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_audit(
        self, project_id: str, plan_execution_id: str
    ) -> tuple[ExecutionAuditRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(execution_audit)
                .where(
                    execution_audit.c.project_id == project_id,
                    execution_audit.c.plan_execution_id == plan_execution_id,
                )
                .order_by(execution_audit.c.created_at)
            ).mappings()
            return tuple(
                ExecutionAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    plan_execution_id=str(row["plan_execution_id"]),
                    event_type=str(row["event_type"]),
                    payload=load(row["payload_json"]),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )
