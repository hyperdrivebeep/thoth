"""One append-only exposure owner with CAS, overlap exclusion and baseline history."""

from collections.abc import Mapping
from typing import cast

from sqlalchemy import Engine, func, insert, select

from thoth.adapters.storage.behavior_execution_schema import (
    behavior_baseline_history,
    behavior_exposure_history,
)
from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    read_connection,
    write_connection,
)
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_execution import BehaviorBaselineRevision, BehaviorExposureRecord
from thoth.domain.behavior_policy import BehaviorPolicyError

_TRANSITIONS: dict[str, set[str]] = {
    "PREPARED": {"APPROVED", "ARMED", "REJECTED", "SKIPPED"},
    "APPROVED": {"ARMED", "REJECTED", "ROLLED_BACK"},
    "ARMED": {"ACTIVE", "ROLLED_BACK", "SKIPPED"},
    "ACTIVE": {"ACTIVE", "COMPLETED", "ROLLED_BACK"},
    "COMPLETED": {"ROLLED_BACK"},
    "REJECTED": set(),
    "ROLLED_BACK": {"ROLLED_BACK"},
    "SKIPPED": set(),
}


class SqliteBehaviorExecutionStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _exposure(row: Mapping[str, object]) -> BehaviorExposureRecord:
        record = BehaviorExposureRecord.model_validate_json(str(row["content_json"]))
        indexed = {
            "project_id": record.spec.project_id,
            "exposure_id": record.spec.exposure_id,
            "revision": record.revision,
            "scope_digest": record.spec.scope_digest,
            "stage": record.spec.stage,
            "state": record.state,
            "record_digest": record.record_digest,
        }
        if any(row[key] != value for key, value in indexed.items()):
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_PROJECTION_MISMATCH")
        return record

    def read(self, project_id: str, exposure_id: str) -> BehaviorExposureRecord | None:
        table = behavior_exposure_history
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(table)
                    .where(
                        table.c.project_id == project_id,
                        table.c.exposure_id == exposure_id,
                    )
                    .order_by(table.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return None if row is None else self._exposure(cast(Mapping[str, object], row))

    def list(self, project_id: str) -> tuple[BehaviorExposureRecord, ...]:
        table = behavior_exposure_history
        latest = (
            select(table.c.exposure_id, func.max(table.c.revision).label("revision"))
            .where(
                table.c.project_id == project_id,
            )
            .group_by(table.c.exposure_id)
            .subquery()
        )
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(table)
                    .join(
                        latest,
                        (table.c.exposure_id == latest.c.exposure_id)
                        & (table.c.revision == latest.c.revision),
                    )
                    .where(table.c.project_id == project_id)
                    .order_by(table.c.exposure_id)
                )
                .mappings()
                .all()
            )
        return tuple(self._exposure(cast(Mapping[str, object], row)) for row in rows)

    def append(self, record: BehaviorExposureRecord, *, expected_revision: int) -> None:
        record = BehaviorExposureRecord.model_validate_json(record.model_dump_json())
        if record.revision != expected_revision + 1:
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_REVISION_INVALID")
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            previous = self.read(record.spec.project_id, record.spec.exposure_id)
            if (0 if previous is None else previous.revision) != expected_revision:
                raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_REVISION_CONFLICT")
            if previous is None:
                if record.state != "PREPARED":
                    raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_INITIAL_STATE_INVALID")
            else:
                self._require_transition(previous, record)
            if (
                record.state in {"ARMED", "ACTIVE"}
                and record.spec.stage == "CANARY"
                and any(
                    item.spec.exposure_id != record.spec.exposure_id
                    and item.spec.scope_digest == record.spec.scope_digest
                    and item.spec.stage == "CANARY"
                    and item.state in {"ARMED", "ACTIVE"}
                    for item in self.list(record.spec.project_id)
                )
            ):
                raise BehaviorPolicyError("BEHAVIOR_CANARY_OVERLAP")
            connection.execute(
                insert(behavior_exposure_history).values(
                    project_id=record.spec.project_id,
                    exposure_id=record.spec.exposure_id,
                    revision=record.revision,
                    scope_digest=record.spec.scope_digest,
                    stage=record.spec.stage,
                    state=record.state,
                    record_digest=record.record_digest,
                    content_json=record.model_dump_json(),
                )
            )

    @staticmethod
    def _require_transition(
        previous: BehaviorExposureRecord, record: BehaviorExposureRecord
    ) -> None:
        if previous.spec != record.spec or record.state not in _TRANSITIONS[previous.state]:
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_TRANSITION_INVALID")
        if previous.approval is not None and previous.approval != record.approval:
            raise BehaviorPolicyError("BEHAVIOR_APPROVAL_IMMUTABLE")
        if previous.state == "ROLLED_BACK" and (
            record.request_count != previous.request_count
            or record.model_call_count != previous.model_call_count
            or record.reserved_cost_microunits != previous.reserved_cost_microunits
            or record.reason_code != previous.reason_code
        ):
            raise BehaviorPolicyError("BEHAVIOR_CLOSED_EXPOSURE_IMMUTABLE")
        if record.observations[: len(previous.observations)] != previous.observations:
            raise BehaviorPolicyError("BEHAVIOR_OBSERVATIONS_IMMUTABLE")
        if record.stage_evidence[: len(previous.stage_evidence)] != previous.stage_evidence:
            raise BehaviorPolicyError("BEHAVIOR_STAGE_EVIDENCE_IMMUTABLE")
        if (
            record.request_count < previous.request_count
            or record.model_call_count < previous.model_call_count
            or record.reserved_cost_microunits < previous.reserved_cost_microunits
        ):
            raise BehaviorPolicyError("BEHAVIOR_BUDGET_COUNTER_REVERSED")
        if previous.started_at is not None and (
            record.started_at != previous.started_at or record.expires_at != previous.expires_at
        ):
            raise BehaviorPolicyError("BEHAVIOR_EXPOSURE_TIME_IMMUTABLE")

    def baseline(
        self, project_id: str, component: BehaviorArtifactKind, environment: str, scope_digest: str
    ) -> BehaviorBaselineRevision | None:
        table = behavior_baseline_history
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(table)
                    .where(
                        table.c.project_id == project_id,
                        table.c.component == component.value,
                        table.c.environment == environment,
                        table.c.scope_digest == scope_digest,
                    )
                    .order_by(table.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return self._baseline(cast(Mapping[str, object], row))

    @staticmethod
    def _baseline(row: Mapping[str, object]) -> BehaviorBaselineRevision:
        record = BehaviorBaselineRevision.model_validate_json(str(row["content_json"]))
        indexed = {
            "project_id": record.project_id,
            "component": record.component.value,
            "environment": record.environment,
            "scope_digest": record.scope_digest,
            "revision": record.revision,
            "content_digest": record.content_digest,
            "receipt_digest": record.receipt_digest,
        }
        if any(row[key] != value for key, value in indexed.items()):
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_PROJECTION_MISMATCH")
        return record

    def baselines(
        self, project_id: str, component: BehaviorArtifactKind, environment: str
    ) -> tuple[BehaviorBaselineRevision, ...]:
        table = behavior_baseline_history
        latest = (
            select(table.c.scope_digest, func.max(table.c.revision).label("revision"))
            .where(
                table.c.project_id == project_id,
                table.c.component == component.value,
                table.c.environment == environment,
            )
            .group_by(table.c.scope_digest)
            .subquery()
        )
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(table)
                    .join(
                        latest,
                        (table.c.scope_digest == latest.c.scope_digest)
                        & (table.c.revision == latest.c.revision),
                    )
                    .where(
                        table.c.project_id == project_id,
                        table.c.component == component.value,
                        table.c.environment == environment,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(self._baseline(cast(Mapping[str, object], row)) for row in rows)

    def promote(self, record: BehaviorBaselineRevision, *, expected_revision: int) -> None:
        record = BehaviorBaselineRevision.model_validate_json(record.model_dump_json())
        if record.change_kind != "PROMOTED":
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_TRANSITION_INVALID")
        if record.revision != expected_revision + 1:
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_REVISION_INVALID")
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            previous = self.baseline(
                record.project_id, record.component, record.environment, record.scope_digest
            )
            if (0 if previous is None else previous.revision) != expected_revision:
                raise BehaviorPolicyError("BEHAVIOR_BASELINE_REVISION_CONFLICT")
            if previous is not None and previous.content_digest != record.previous_digest:
                raise BehaviorPolicyError("BEHAVIOR_BASELINE_CHANGED")
            exposure = self.read(record.project_id, record.exposure_ref)
            if (
                exposure is None
                or exposure.state != "COMPLETED"
                or exposure.spec.stage != "CANARY"
                or exposure.spec.candidate_artifact_ref != record.artifact_ref
                or exposure.spec.candidate_digest != record.content_digest
                or exposure.spec.component != record.component
                or exposure.spec.environment != record.environment
                or exposure.spec.target_thread_id != record.target_thread_id
                or exposure.spec.target_workstream != record.target_workstream
                or exposure.spec.baseline_digest != record.previous_digest
            ):
                raise BehaviorPolicyError("BEHAVIOR_CANARY_EVIDENCE_REQUIRED")
            connection.execute(
                insert(behavior_baseline_history).values(
                    project_id=record.project_id,
                    component=record.component.value,
                    environment=record.environment,
                    scope_digest=record.scope_digest,
                    revision=record.revision,
                    content_digest=record.content_digest,
                    receipt_digest=record.receipt_digest,
                    content_json=record.model_dump_json(),
                )
            )

    def rollback_baseline(
        self, record: BehaviorBaselineRevision, *, expected_revision: int
    ) -> None:
        record = BehaviorBaselineRevision.model_validate_json(record.model_dump_json())
        if record.change_kind != "ROLLED_BACK" or record.revision != expected_revision + 1:
            raise BehaviorPolicyError("BEHAVIOR_BASELINE_TRANSITION_INVALID")
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            current = self.baseline(
                record.project_id, record.component, record.environment, record.scope_digest
            )
            exposure = self.read(record.project_id, record.exposure_ref)
            if (
                current is None
                or current.revision != expected_revision
                or current.exposure_ref != record.exposure_ref
                or current.content_digest != record.previous_digest
            ):
                raise BehaviorPolicyError("BEHAVIOR_BASELINE_REVISION_CONFLICT")
            if (
                exposure is None
                or exposure.state != "ROLLED_BACK"
                or exposure.spec.baseline_artifact_ref != record.artifact_ref
                or exposure.spec.baseline_digest != record.content_digest
                or exposure.spec.component != record.component
                or exposure.spec.environment != record.environment
                or exposure.spec.target_thread_id != record.target_thread_id
                or exposure.spec.target_workstream != record.target_workstream
            ):
                raise BehaviorPolicyError("BEHAVIOR_ROLLBACK_BINDING_MISMATCH")
            connection.execute(
                insert(behavior_baseline_history).values(
                    project_id=record.project_id,
                    component=record.component.value,
                    environment=record.environment,
                    scope_digest=record.scope_digest,
                    revision=record.revision,
                    content_digest=record.content_digest,
                    receipt_digest=record.receipt_digest,
                    content_json=record.model_dump_json(),
                )
            )
