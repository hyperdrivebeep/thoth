from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, Integer, Table, insert, literal_column, select
from sqlalchemy.sql.base import Executable

from thoth.adapters.storage.research_identity import canonical_records
from thoth.adapters.storage.schema import (
    hypothesis_appraisals,
    hypothesis_assumptions,
    hypothesis_audit,
    hypothesis_portfolios,
    hypothesis_predictions,
    hypothesis_records,
    hypothesis_relations,
    hypothesis_test_bindings,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis_full import (
    AuxiliaryAssumptionRecord,
    HypothesisAppraisalRecord,
    HypothesisAuditRecord,
    HypothesisPortfolioRecord,
    HypothesisRecord,
    HypothesisRelationRecord,
    HypothesisTestBinding,
    PredictionRecord,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.resource_scope import ResourceAccessPort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored hypothesis payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteHypothesisStore(HypothesisStorePort):
    def __init__(
        self,
        engine: Engine,
        ledger: LedgerPort | None = None,
        resource_access: ResourceAccessPort | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._resource_access = resource_access

    def _add(
        self,
        table: Table,
        value: DomainModel,
        **columns: object,
    ) -> None:
        created_at = getattr(value, "created_at", None)
        if not isinstance(created_at, datetime):
            raise TypeError("stored hypothesis record requires created_at")
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(table).values(
                    **columns,
                    content_json=dump(value.model_dump(mode="json")),
                    created_at=created_at.isoformat(),
                )
            )

    def add_hypothesis(self, value: HypothesisRecord) -> None:
        self._add(
            hypothesis_records,
            value,
            hypothesis_revision_id=value.hypothesis_revision_id,
            hypothesis_id=value.hypothesis_id,
            project_id=value.project_id,
            object_id=value.object_id,
            portfolio_id=value.portfolio_id,
            revision_digest=value.revision_digest,
        )

    def list_hypotheses(self, project_id: str) -> tuple[HypothesisRecord, ...]:
        if self._ledger is not None:
            return self._canonical_records(
                project_id, hypothesis_records, "hypothesis_id", HypothesisRecord
            )
        return self._latest_hypotheses(
            self._list(hypothesis_records, "project_id", project_id, HypothesisRecord)
        )

    def read_hypothesis(
        self, project_id: str, hypothesis_id: str, revision_digest: str | None
    ) -> HypothesisRecord | None:
        if self._ledger is not None:
            records = self._canonical_records(
                project_id,
                hypothesis_records,
                "hypothesis_id",
                HypothesisRecord,
                identifier=hypothesis_id,
                revision_digest=revision_digest,
            )
            return records[0] if records else None
        statement = select(hypothesis_records).where(
            hypothesis_records.c.project_id == project_id,
            hypothesis_records.c.hypothesis_id == hypothesis_id,
        )
        if revision_digest is None:
            statement = statement.order_by(
                hypothesis_records.c.created_at.desc(), literal_column("rowid", Integer()).desc()
            ).limit(1)
        else:
            statement = statement.where(hypothesis_records.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else HypothesisRecord.model_validate(load(row["content_json"]))

    def add_portfolio(self, value: HypothesisPortfolioRecord) -> None:
        self._add(
            hypothesis_portfolios,
            value,
            portfolio_revision_id=value.portfolio_revision_id,
            portfolio_id=value.portfolio_id,
            project_id=value.project_id,
            object_id=value.object_id,
            revision_digest=value.revision_digest,
        )

    def list_portfolios(self, project_id: str) -> tuple[HypothesisPortfolioRecord, ...]:
        if self._ledger is not None:
            return self._canonical_records(
                project_id, hypothesis_portfolios, "portfolio_id", HypothesisPortfolioRecord
            )
        values = self._list(
            hypothesis_portfolios,
            "project_id",
            project_id,
            HypothesisPortfolioRecord,
        )
        latest: dict[str, HypothesisPortfolioRecord] = {}
        for value in values:
            latest[value.portfolio_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_portfolio(
        self, project_id: str, portfolio_id: str, revision_digest: str | None
    ) -> HypothesisPortfolioRecord | None:
        if self._ledger is not None:
            records = self._canonical_records(
                project_id,
                hypothesis_portfolios,
                "portfolio_id",
                HypothesisPortfolioRecord,
                identifier=portfolio_id,
                revision_digest=revision_digest,
            )
            return records[0] if records else None
        statement = select(hypothesis_portfolios).where(
            hypothesis_portfolios.c.project_id == project_id,
            hypothesis_portfolios.c.portfolio_id == portfolio_id,
        )
        if revision_digest is None:
            statement = statement.order_by(
                hypothesis_portfolios.c.created_at.desc(), literal_column("rowid", Integer()).desc()
            ).limit(1)
        else:
            statement = statement.where(hypothesis_portfolios.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return (
            None
            if row is None
            else HypothesisPortfolioRecord.model_validate(load(row["content_json"]))
        )

    def add_relation(self, value: HypothesisRelationRecord) -> None:
        self._add(
            hypothesis_relations,
            value,
            relation_revision_id=value.relation_revision_id,
            relation_id=value.relation_id,
            project_id=value.project_id,
            portfolio_id=value.portfolio_id,
            revision_digest=value.revision_digest,
        )

    def list_relations(
        self, project_id: str, portfolio_id: str | None
    ) -> tuple[HypothesisRelationRecord, ...]:
        statement = (
            select(hypothesis_relations)
            .order_by(hypothesis_relations.c.created_at, literal_column("rowid", Integer()))
            .where(hypothesis_relations.c.project_id == project_id)
        )
        if portfolio_id is not None:
            statement = statement.where(hypothesis_relations.c.portfolio_id == portfolio_id)
        values = self._execute(statement, HypothesisRelationRecord)
        latest: dict[str, HypothesisRelationRecord] = {}
        for value in values:
            latest[value.relation_id] = value
        return tuple(latest[key] for key in sorted(latest))

    def read_relation(self, relation_id: str) -> HypothesisRelationRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(hypothesis_relations)
                    .where(hypothesis_relations.c.relation_id == relation_id)
                    .order_by(
                        hypothesis_relations.c.created_at.desc(),
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
            else HypothesisRelationRecord.model_validate(load(row["content_json"]))
        )
        if result is not None:
            self._require_auxiliary(result)
        return result

    def add_assumption(self, value: AuxiliaryAssumptionRecord) -> None:
        self._add(
            hypothesis_assumptions,
            value,
            assumption_id=value.assumption_id,
            project_id=value.project_id,
            hypothesis_id=value.hypothesis_id,
            assumption_digest=value.assumption_digest,
        )

    def list_assumptions(
        self, project_id: str, hypothesis_id: str | None
    ) -> tuple[AuxiliaryAssumptionRecord, ...]:
        statement = select(hypothesis_assumptions).where(
            hypothesis_assumptions.c.project_id == project_id
        )
        if hypothesis_id is not None:
            statement = statement.where(hypothesis_assumptions.c.hypothesis_id == hypothesis_id)
        return self._execute(statement, AuxiliaryAssumptionRecord)

    def add_prediction(self, value: PredictionRecord) -> None:
        self._add(
            hypothesis_predictions,
            value,
            prediction_id=value.prediction_id,
            project_id=value.project_id,
            hypothesis_id=value.hypothesis_id,
            prediction_digest=value.prediction_digest,
        )

    def list_predictions(
        self, project_id: str, hypothesis_id: str | None
    ) -> tuple[PredictionRecord, ...]:
        statement = select(hypothesis_predictions).where(
            hypothesis_predictions.c.project_id == project_id
        )
        if hypothesis_id is not None:
            statement = statement.where(hypothesis_predictions.c.hypothesis_id == hypothesis_id)
        return self._execute(statement, PredictionRecord)

    def read_prediction(self, prediction_id: str) -> PredictionRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(hypothesis_predictions).where(
                        hypothesis_predictions.c.prediction_id == prediction_id
                    )
                )
                .mappings()
                .first()
            )
        result = None if row is None else PredictionRecord.model_validate(load(row["content_json"]))
        if result is not None:
            self._require_auxiliary(result)
        return result

    def add_test_binding(self, value: HypothesisTestBinding) -> None:
        self._add(
            hypothesis_test_bindings,
            value,
            test_binding_id=value.test_binding_id,
            project_id=value.project_id,
            prediction_id=value.prediction_id,
            binding_digest=value.binding_digest,
        )

    def list_test_bindings(
        self, project_id: str, prediction_id: str | None
    ) -> tuple[HypothesisTestBinding, ...]:
        statement = select(hypothesis_test_bindings).where(
            hypothesis_test_bindings.c.project_id == project_id
        )
        if prediction_id is not None:
            statement = statement.where(hypothesis_test_bindings.c.prediction_id == prediction_id)
        return self._execute(statement, HypothesisTestBinding)

    def add_appraisal(self, value: HypothesisAppraisalRecord) -> None:
        self._add(
            hypothesis_appraisals,
            value,
            appraisal_id=value.appraisal_id,
            project_id=value.project_id,
            hypothesis_id=value.hypothesis_id,
            appraisal_digest=value.appraisal_digest,
        )

    def list_appraisals(
        self, project_id: str, hypothesis_id: str
    ) -> tuple[HypothesisAppraisalRecord, ...]:
        statement = select(hypothesis_appraisals).where(
            hypothesis_appraisals.c.project_id == project_id,
            hypothesis_appraisals.c.hypothesis_id == hypothesis_id,
        )
        return self._execute(statement, HypothesisAppraisalRecord)

    def append_audit(self, value: HypothesisAuditRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(hypothesis_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    hypothesis_id=value.hypothesis_id,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_audit(self, project_id: str, hypothesis_id: str) -> tuple[HypothesisAuditRecord, ...]:
        if self._resource_access is not None:
            self._require_current_hypothesis(project_id, hypothesis_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(hypothesis_audit)
                .where(
                    hypothesis_audit.c.project_id == project_id,
                    hypothesis_audit.c.hypothesis_id == hypothesis_id,
                )
                .order_by(hypothesis_audit.c.created_at)
            ).mappings()
            return tuple(
                HypothesisAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    hypothesis_id=str(row["hypothesis_id"]),
                    event_type=str(row["event_type"]),
                    payload=load(row["payload_json"]),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    def _canonical_records[T: DomainModel](
        self,
        project_id: str,
        table: Table,
        id_field: str,
        model: type[T],
        *,
        identifier: str | None = None,
        revision_digest: str | None = None,
    ) -> tuple[T, ...]:
        assert self._ledger is not None
        return canonical_records(
            self._ledger,
            self._engine,
            EntityType.HYPOTHESIS,
            project_id,
            table,
            id_field,
            model,
            identifier=identifier,
            revision_digest=revision_digest,
            resource_access=self._resource_access,
        )

    def _list[T: DomainModel](
        self, table: Table, field: str, value: str, model: type[T]
    ) -> tuple[T, ...]:
        return self._execute(
            select(table)
            .where(table.c[field] == value)
            .order_by(table.c.created_at, literal_column("rowid", Integer())),
            model,
        )

    def _execute[T: DomainModel](self, statement: Executable, model: type[T]) -> tuple[T, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(statement).mappings()
            values = tuple(model.model_validate(load(row["content_json"])) for row in rows)
        # Close the query connection before entering the current-permission transaction.
        return tuple(value for value in values if self._may_read_auxiliary(value))

    def _require_current_hypothesis(self, project_id: str, identifier: str) -> None:
        assert self._resource_access is not None
        digest = (
            None
            if self._ledger is None
            else self._ledger.read_heads(project_id).get(f"HYPOTHESIS:{identifier}")
        )
        if digest is None:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        self._resource_access.require_revision(project_id, digest)

    def _require_auxiliary(self, value: DomainModel) -> None:
        access = self._resource_access
        if access is None:
            return
        if isinstance(value, HypothesisRecord | HypothesisPortfolioRecord):
            access.require_revision(value.project_id, value.revision_digest)
        elif isinstance(value, AuxiliaryAssumptionRecord):
            self._require_current_hypothesis(value.project_id, value.hypothesis_id)
            for ref in value.evidence_refs:
                access.require_read(value.project_id, ref)
        elif isinstance(value, PredictionRecord | HypothesisAppraisalRecord):
            access.require_revision(value.project_id, value.hypothesis_revision_digest)
            if isinstance(value, HypothesisAppraisalRecord):
                for ref in value.evidence_refs:
                    access.require_read(value.project_id, ref)
            else:
                access.require_read(value.project_id, value.measurement_contract_ref)
                allowed = {
                    r.assumption_id
                    for r in self.list_assumptions(value.project_id, value.hypothesis_id)
                }
                if not set(value.assumption_refs).issubset(allowed):
                    raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        elif isinstance(value, HypothesisRelationRecord):
            for identifier in (value.source_hypothesis_id, value.target_hypothesis_id):
                self._require_current_hypothesis(value.project_id, identifier)
            for ref in value.evidence_refs:
                access.require_read(value.project_id, ref)
        elif isinstance(value, HypothesisTestBinding):
            prediction = self.read_prediction(value.prediction_id)
            if prediction is None or prediction.project_id != value.project_id:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            for ref in value.observation_refs:
                access.require_read(value.project_id, ref)

    def _may_read_auxiliary(self, value: DomainModel) -> bool:
        try:
            self._require_auxiliary(value)
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                return False
            raise
        return True

    @staticmethod
    def _latest_hypotheses(values: tuple[HypothesisRecord, ...]) -> tuple[HypothesisRecord, ...]:
        latest: dict[str, HypothesisRecord] = {}
        for value in values:
            latest[value.hypothesis_id] = value
        return tuple(latest[key] for key in sorted(latest))
