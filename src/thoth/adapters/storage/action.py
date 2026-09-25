from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, Integer, Table, insert, literal_column, select

from thoth.adapters.storage.research_identity import canonical_records
from thoth.adapters.storage.schema import (
    action_audit,
    action_authorizations,
    action_plans,
    action_portfolios,
    action_records,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.action_full import (
    ActionAuditRecord,
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType
from thoth.ports.action import ActionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.resource_scope import ResourceAccessPort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored action payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteActionStore(ActionStorePort):
    def __init__(
        self,
        engine: Engine,
        ledger: LedgerPort | None = None,
        resource_access: ResourceAccessPort | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._resource_access = resource_access

    def _add(self, table: Table, value: DomainModel, **columns: object) -> None:
        created_at = getattr(value, "created_at", None)
        if not isinstance(created_at, datetime):
            raise TypeError("stored action record requires created_at")
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(table).values(
                    **columns,
                    content_json=dump(value.model_dump(mode="json")),
                    created_at=created_at.isoformat(),
                )
            )

    def add_action(self, value: ActionRecord) -> None:
        self._add(
            action_records,
            value,
            action_revision_id=value.action_revision_id,
            action_id=value.action_id,
            project_id=value.project_id,
            object_id=value.object_id,
            portfolio_id=value.portfolio_id,
            revision_digest=value.revision_digest,
        )

    def list_actions(self, project_id: str) -> tuple[ActionRecord, ...]:
        if self._ledger is not None:
            return canonical_records(
                self._ledger,
                self._engine,
                EntityType.ACTION,
                project_id,
                action_records,
                "action_id",
                ActionRecord,
                resource_access=self._resource_access,
            )
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(action_records)
                .where(action_records.c.project_id == project_id)
                .order_by(action_records.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            values = tuple(ActionRecord.model_validate(load(row["content_json"])) for row in rows)
        latest: dict[str, ActionRecord] = {}
        for value in values:
            latest[value.action_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_action(
        self, project_id: str, action_id: str, revision_digest: str | None
    ) -> ActionRecord | None:
        if self._ledger is not None:
            rows = canonical_records(
                self._ledger,
                self._engine,
                EntityType.ACTION,
                project_id,
                action_records,
                "action_id",
                ActionRecord,
                identifier=action_id,
                resource_access=self._resource_access,
                revision_digest=revision_digest,
            )
            return rows[0] if rows else None
        statement = select(action_records).where(
            action_records.c.project_id == project_id,
            action_records.c.action_id == action_id,
        )
        if revision_digest is None:
            statement = statement.order_by(
                action_records.c.created_at.desc(), literal_column("rowid", Integer()).desc()
            ).limit(1)
        else:
            statement = statement.where(action_records.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else ActionRecord.model_validate(load(row["content_json"]))

    def add_portfolio(self, value: ActionPortfolioRecord) -> None:
        self._add(
            action_portfolios,
            value,
            portfolio_revision_id=value.portfolio_revision_id,
            portfolio_id=value.portfolio_id,
            project_id=value.project_id,
            object_id=value.object_id,
            revision_digest=value.revision_digest,
        )

    def list_portfolios(self, project_id: str) -> tuple[ActionPortfolioRecord, ...]:
        if self._ledger is not None:
            return canonical_records(
                self._ledger,
                self._engine,
                EntityType.ACTION,
                project_id,
                action_portfolios,
                "portfolio_id",
                ActionPortfolioRecord,
                resource_access=self._resource_access,
            )
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(action_portfolios)
                .where(action_portfolios.c.project_id == project_id)
                .order_by(action_portfolios.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            values = tuple(
                ActionPortfolioRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, ActionPortfolioRecord] = {}
        for value in values:
            latest[value.portfolio_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_portfolio(
        self, project_id: str, portfolio_id: str, revision_digest: str | None
    ) -> ActionPortfolioRecord | None:
        if self._ledger is not None:
            rows = canonical_records(
                self._ledger,
                self._engine,
                EntityType.ACTION,
                project_id,
                action_portfolios,
                "portfolio_id",
                ActionPortfolioRecord,
                identifier=portfolio_id,
                resource_access=self._resource_access,
                revision_digest=revision_digest,
            )
            return rows[0] if rows else None
        statement = select(action_portfolios).where(
            action_portfolios.c.project_id == project_id,
            action_portfolios.c.portfolio_id == portfolio_id,
        )
        if revision_digest is None:
            statement = statement.order_by(
                action_portfolios.c.created_at.desc(), literal_column("rowid", Integer()).desc()
            ).limit(1)
        else:
            statement = statement.where(action_portfolios.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return (
            None if row is None else ActionPortfolioRecord.model_validate(load(row["content_json"]))
        )

    def add_plan(self, value: ActionPlanRecord) -> None:
        self._add(
            action_plans,
            value,
            plan_revision_id=value.plan_revision_id,
            plan_id=value.plan_id,
            project_id=value.project_id,
            object_id=value.object_id,
            revision_digest=value.revision_digest,
        )

    def list_plans(self, project_id: str) -> tuple[ActionPlanRecord, ...]:
        if self._ledger is not None:
            return canonical_records(
                self._ledger,
                self._engine,
                EntityType.ACTION,
                project_id,
                action_plans,
                "plan_id",
                ActionPlanRecord,
                resource_access=self._resource_access,
            )
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(action_plans)
                .where(action_plans.c.project_id == project_id)
                .order_by(action_plans.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            values = tuple(
                ActionPlanRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, ActionPlanRecord] = {}
        for value in values:
            latest[value.plan_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_plan(
        self, project_id: str, plan_id: str, revision_digest: str | None
    ) -> ActionPlanRecord | None:
        if self._ledger is not None:
            rows = canonical_records(
                self._ledger,
                self._engine,
                EntityType.ACTION,
                project_id,
                action_plans,
                "plan_id",
                ActionPlanRecord,
                identifier=plan_id,
                resource_access=self._resource_access,
                revision_digest=revision_digest,
            )
            return rows[0] if rows else None
        statement = select(action_plans).where(
            action_plans.c.project_id == project_id,
            action_plans.c.plan_id == plan_id,
        )
        if revision_digest is None:
            statement = statement.order_by(
                action_plans.c.created_at.desc(), literal_column("rowid", Integer()).desc()
            ).limit(1)
        else:
            statement = statement.where(action_plans.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else ActionPlanRecord.model_validate(load(row["content_json"]))

    def add_authorization(self, value: AuthorizationEnvelopeRecord) -> None:
        self._add(
            action_authorizations,
            value,
            authorization_revision_id=value.authorization_revision_id,
            authorization_id=value.authorization_id,
            project_id=value.project_id,
            plan_id=value.plan_id,
            step_id=value.step_id,
            revision_digest=value.revision_digest,
        )

    def read_authorization(
        self, project_id: str, authorization_id: str
    ) -> AuthorizationEnvelopeRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(action_authorizations)
                    .where(
                        action_authorizations.c.project_id == project_id,
                        action_authorizations.c.authorization_id == authorization_id,
                    )
                    .order_by(
                        action_authorizations.c.created_at.desc(),
                        literal_column("rowid", Integer()).desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        result = (
            None
            if row is None
            else AuthorizationEnvelopeRecord.model_validate(load(row["content_json"]))
        )
        if result is not None and self._resource_access is not None:
            self._resource_access.require_revision(project_id, result.plan_revision_digest)
        return result

    def list_authorizations(
        self, project_id: str, plan_id: str | None
    ) -> tuple[AuthorizationEnvelopeRecord, ...]:
        statement = select(action_authorizations).where(
            action_authorizations.c.project_id == project_id
        )
        if plan_id is not None:
            statement = statement.where(action_authorizations.c.plan_id == plan_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(
                    action_authorizations.c.created_at, literal_column("rowid", Integer())
                )
            ).mappings()
            values = tuple(
                AuthorizationEnvelopeRecord.model_validate(load(row["content_json"]))
                for row in rows
            )
        latest: dict[str, AuthorizationEnvelopeRecord] = {}
        for value in values:
            latest[value.authorization_id] = value
        return tuple(
            latest[key]
            for key in sorted(latest)
            if self._resource_access is None
            or self._resource_access.may_read_revision(project_id, latest[key].plan_revision_digest)
        )

    def append_audit(self, value: ActionAuditRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(action_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    subject_id=value.subject_id,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_audit(self, project_id: str, subject_id: str | None) -> tuple[ActionAuditRecord, ...]:
        statement = select(action_audit).where(action_audit.c.project_id == project_id)
        if subject_id is not None:
            statement = statement.where(action_audit.c.subject_id == subject_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(action_audit.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            values = tuple(
                ActionAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    subject_id=str(row["subject_id"]),
                    event_type=str(row["event_type"]),
                    payload=load(row["payload_json"]),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )
        if self._resource_access is None:
            return values
        if self._ledger is None:
            return ()
        heads = self._ledger.read_heads(project_id)
        return tuple(
            item
            for item in values
            if (digest := heads.get(f"ACTION:{item.subject_id}")) is not None
            and self._resource_access.may_read_revision(project_id, digest)
        )
