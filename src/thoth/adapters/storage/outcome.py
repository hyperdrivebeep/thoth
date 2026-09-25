from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, Table, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import (
    outcome_assessments,
    outcome_attributions,
    outcome_audit,
    outcome_change_sets,
    outcome_impacts,
    outcome_profiles,
    outcome_series,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.base import DomainModel
from thoth.domain.outcome_full import (
    AttributionAssessmentRecord,
    OutcomeAssessmentRecord,
    OutcomeAuditRecord,
    OutcomeChangeSetRecord,
    OutcomeImpactRecord,
    OutcomeProfileRecord,
    OutcomeSeriesRecord,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.resource_scope import ResourceAccessPort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored Outcome payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteOutcomeStore(OutcomeStorePort):
    def __init__(self, engine: Engine, resource_access: ResourceAccessPort | None = None) -> None:
        self._engine = engine
        self._resource_access = resource_access

    def _add(self, table: Table, value: DomainModel, **columns: object) -> None:
        created_at = getattr(value, "created_at", None)
        if not isinstance(created_at, datetime):
            raise TypeError("stored Outcome record requires created_at")
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(table).values(
                    **columns,
                    content_json=dump(value.model_dump(mode="json")),
                    created_at=created_at.isoformat(),
                )
            )

    def put_profile(self, value: OutcomeProfileRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                sqlite_insert(outcome_profiles)
                .values(
                    profile_ref=value.profile_ref,
                    version=value.version,
                    content_json=dump(value.model_dump(mode="json")),
                    enabled=int(value.enabled),
                    profile_digest=value.profile_digest,
                )
                .on_conflict_do_nothing(index_elements=["profile_ref", "version"])
            )

    def list_profiles(self, enabled_only: bool) -> tuple[OutcomeProfileRecord, ...]:
        statement = select(outcome_profiles)
        if enabled_only:
            statement = statement.where(outcome_profiles.c.enabled == 1)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(outcome_profiles.c.profile_ref, outcome_profiles.c.version)
            ).mappings()
            return tuple(
                OutcomeProfileRecord.model_validate(load(row["content_json"])) for row in rows
            )

    def read_profile(self, profile_ref: str, version: int | None) -> OutcomeProfileRecord | None:
        statement = select(outcome_profiles).where(outcome_profiles.c.profile_ref == profile_ref)
        if version is None:
            statement = statement.order_by(outcome_profiles.c.version.desc()).limit(1)
        else:
            statement = statement.where(outcome_profiles.c.version == version)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return (
            None if row is None else OutcomeProfileRecord.model_validate(load(row["content_json"]))
        )

    def add_series(self, value: OutcomeSeriesRecord) -> None:
        self._add(
            outcome_series,
            value,
            series_revision_id=value.series_revision_id,
            outcome_series_id=value.outcome_series_id,
            project_id=value.project_id,
            object_id=value.object_id,
            revision_digest=value.revision_digest,
        )

    def list_series(self, project_id: str) -> tuple[OutcomeSeriesRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(outcome_series)
                .where(outcome_series.c.project_id == project_id)
                .order_by(outcome_series.c.created_at)
            ).mappings()
            values = tuple(
                OutcomeSeriesRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, OutcomeSeriesRecord] = {}
        for value in values:
            latest[value.outcome_series_id] = value
        return tuple(latest[key] for key in sorted(latest) if self._allowed(latest[key]))

    def read_series(
        self, project_id: str, series_id: str, revision_digest: str | None
    ) -> OutcomeSeriesRecord | None:
        statement = select(outcome_series).where(
            outcome_series.c.project_id == project_id,
            outcome_series.c.outcome_series_id == series_id,
        )
        if revision_digest is None:
            statement = statement.order_by(outcome_series.c.created_at.desc()).limit(1)
        else:
            statement = statement.where(outcome_series.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return self._checked(
            None if row is None else OutcomeSeriesRecord.model_validate(load(row["content_json"]))
        )

    def add_assessment(self, value: OutcomeAssessmentRecord) -> None:
        self._add(
            outcome_assessments,
            value,
            assessment_revision_id=value.assessment_revision_id,
            outcome_assessment_id=value.outcome_assessment_id,
            project_id=value.project_id,
            outcome_series_id=value.outcome_series_id,
            object_id=value.object_id,
            revision_digest=value.revision_digest,
        )

    def list_assessments(self, project_id: str) -> tuple[OutcomeAssessmentRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(outcome_assessments)
                .where(outcome_assessments.c.project_id == project_id)
                .order_by(outcome_assessments.c.created_at)
            ).mappings()
            values = tuple(
                OutcomeAssessmentRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, OutcomeAssessmentRecord] = {}
        for value in values:
            latest[value.outcome_assessment_id] = value
        return tuple(latest[key] for key in sorted(latest) if self._allowed(latest[key]))

    def read_assessment(
        self, project_id: str, assessment_id: str, revision_digest: str | None
    ) -> OutcomeAssessmentRecord | None:
        statement = select(outcome_assessments).where(
            outcome_assessments.c.project_id == project_id,
            outcome_assessments.c.outcome_assessment_id == assessment_id,
        )
        if revision_digest is None:
            statement = statement.order_by(outcome_assessments.c.created_at.desc()).limit(1)
        else:
            statement = statement.where(outcome_assessments.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return self._checked(
            None
            if row is None
            else OutcomeAssessmentRecord.model_validate(load(row["content_json"]))
        )

    def add_attribution(self, value: AttributionAssessmentRecord) -> None:
        self._add(
            outcome_attributions,
            value,
            attribution_assessment_id=value.attribution_assessment_id,
            project_id=value.project_id,
            outcome_assessment_id=value.outcome_assessment_id,
            attribution_digest=value.attribution_digest,
        )

    def read_attribution(
        self, project_id: str, attribution_id: str
    ) -> AttributionAssessmentRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(outcome_attributions).where(
                        outcome_attributions.c.project_id == project_id,
                        outcome_attributions.c.attribution_assessment_id == attribution_id,
                    )
                )
                .mappings()
                .first()
            )
        return self._checked(
            None
            if row is None
            else AttributionAssessmentRecord.model_validate(load(row["content_json"]))
        )

    def add_change_set(self, value: OutcomeChangeSetRecord) -> None:
        self._add(
            outcome_change_sets,
            value,
            outcome_change_set_id=value.outcome_change_set_id,
            project_id=value.project_id,
            outcome_assessment_id=value.outcome_assessment_id,
            change_set_digest=value.change_set_digest,
        )

    def read_change_set(self, project_id: str, change_set_id: str) -> OutcomeChangeSetRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(outcome_change_sets).where(
                        outcome_change_sets.c.project_id == project_id,
                        outcome_change_sets.c.outcome_change_set_id == change_set_id,
                    )
                )
                .mappings()
                .first()
            )
        return self._checked(
            None
            if row is None
            else OutcomeChangeSetRecord.model_validate(load(row["content_json"]))
        )

    def add_impact(self, value: OutcomeImpactRecord) -> None:
        self._add(
            outcome_impacts,
            value,
            impact_assessment_id=value.impact_assessment_id,
            project_id=value.project_id,
            outcome_series_id=value.outcome_series_id,
            impact_digest=value.impact_digest,
        )

    def read_impact(self, project_id: str, impact_id: str) -> OutcomeImpactRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(outcome_impacts).where(
                        outcome_impacts.c.project_id == project_id,
                        outcome_impacts.c.impact_assessment_id == impact_id,
                    )
                )
                .mappings()
                .first()
            )
        return self._checked(
            None if row is None else OutcomeImpactRecord.model_validate(load(row["content_json"]))
        )

    def append_audit(self, value: OutcomeAuditRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(outcome_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    subject_id=value.subject_id,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_audit(self, project_id: str, subject_id: str | None) -> tuple[OutcomeAuditRecord, ...]:
        statement = select(outcome_audit).where(outcome_audit.c.project_id == project_id)
        if subject_id is not None:
            statement = statement.where(outcome_audit.c.subject_id == subject_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(statement.order_by(outcome_audit.c.created_at)).mappings()
            values = tuple(
                OutcomeAuditRecord(
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
        return tuple(item for item in values if self._allowed(item))

    def _checked[T: DomainModel](self, value: T | None) -> T | None:
        if value is not None:
            self._require(value)
        return value

    def _require(self, value: DomainModel) -> None:
        access = self._resource_access
        if access is None:
            return
        if isinstance(value, OutcomeSeriesRecord):
            access.require_revision(value.project_id, value.revision_digest)
            access.require_reads(
                value.project_id,
                tuple(ref for refs in value.observation_refs_by_phase.values() for ref in refs),
            )
        elif isinstance(value, OutcomeAssessmentRecord):
            access.require_revision(value.project_id, value.revision_digest)
            access.require_reads(
                value.project_id, (*value.actual_observation_refs, *value.comparator_refs)
            )
        elif isinstance(value, AttributionAssessmentRecord | OutcomeChangeSetRecord):
            if self.read_assessment(value.project_id, value.outcome_assessment_id, None) is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            if isinstance(value, AttributionAssessmentRecord):
                access.require_reads(
                    value.project_id, (*value.evidence_refs, *value.counterfactual_evidence_refs)
                )
        elif isinstance(value, OutcomeImpactRecord):
            if self.read_series(value.project_id, value.outcome_series_id, None) is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            access.require_reads(value.project_id, value.evidence_refs)
        elif isinstance(value, OutcomeAuditRecord):
            if (
                self.read_series(value.project_id, value.subject_id, None) is None
                and self.read_assessment(value.project_id, value.subject_id, None) is None
            ):
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")

    def _allowed(self, value: DomainModel) -> bool:
        try:
            self._require(value)
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
