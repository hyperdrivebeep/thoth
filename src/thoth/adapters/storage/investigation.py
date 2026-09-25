from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, insert, select, update

from thoth.adapters.storage.schema import investigation_audit, investigations
from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    read_connection,
    write_connection,
)
from thoth.domain.investigation import InvestigationAuditRecord, InvestigationRecord
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
from thoth.ports.investigation import InvestigationStorePort
from thoth.ports.resource_scope import ResourceRecordAccessPort


def _object(value: str) -> dict[str, object]:
    loaded = cast(object, orjson.loads(value))
    if not isinstance(loaded, dict):
        raise ValueError("stored investigation payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], loaded).items()}


def _tuple(value: str) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(list[object], orjson.loads(value)))


class SqliteInvestigationStore(InvestigationStorePort):
    def __init__(
        self, engine: Engine, resource_access: ResourceRecordAccessPort | None = None
    ) -> None:
        self._engine = engine
        self._access = resource_access

    def _seal(self, project_id: str, digest: str, parents: tuple[str, ...] = ()) -> None:
        if self._access is None:
            return
        self._access.require_reads(project_id, parents)
        refs = (
            *parents,
            *(
                use.resource_ref
                for use in current_resource_uses() or ()
                if use.project_id == project_id
            ),
        )
        self._access.record_control_lineage(project_id, digest, refs)

    def _require(self, project_id: str, digest: str) -> None:
        if self._access is not None:
            self._access.require_read(project_id, f"control:{digest}")

    def _visible(self, project_id: str, digest: str) -> bool:
        return self._access is None or self._access.may_read(project_id, f"control:{digest}")

    def create(self, value: InvestigationRecord) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            parents: tuple[str, ...] = ()
            if self._access is not None and value.parent_investigation_id is not None:
                parent = self.read(value.parent_investigation_id)
                if parent is None or parent.project_id != value.project_id:
                    raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
                parents = (f"control:{parent.investigation_digest}",)
            connection.execute(insert(investigations).values(**self._values(value)))
            self._seal(value.project_id, value.investigation_digest, parents)

    def read(self, investigation_id: str) -> InvestigationRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(investigations).where(
                        investigations.c.investigation_id == investigation_id
                    )
                )
                .mappings()
                .first()
            )
        value = None if row is None else self._record(row)
        if value is not None:
            self._require(value.project_id, value.investigation_digest)
        return value

    def list(
        self, project_id: str, thread_id: str | None = None
    ) -> tuple[InvestigationRecord, ...]:
        statement = select(investigations).where(investigations.c.project_id == project_id)
        if thread_id is not None:
            statement = statement.where(investigations.c.thread_id == thread_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(investigations.c.created_at, investigations.c.investigation_id)
            ).mappings()
            values = tuple(self._record(row) for row in rows)
        return tuple(
            value for value in values if self._visible(project_id, value.investigation_digest)
        )

    def update(self, value: InvestigationRecord, *, expected_plan_revision: int) -> bool:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            previous = self.read(value.investigation_id)
            if previous is None:
                return False
            if previous.project_id != value.project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            changed = connection.execute(
                update(investigations)
                .where(
                    investigations.c.investigation_id == value.investigation_id,
                    investigations.c.plan_revision == expected_plan_revision,
                )
                .values(**self._values(value))
            ).rowcount
            if changed == 1:
                self._seal(
                    value.project_id,
                    value.investigation_digest,
                    (f"control:{previous.investigation_digest}",),
                )
        return changed == 1

    def append_audit(self, value: InvestigationAuditRecord) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            connection.execute(
                insert(investigation_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    investigation_id=value.investigation_id,
                    event_type=value.event_type,
                    payload_json=orjson.dumps(value.payload, option=orjson.OPT_SORT_KEYS).decode(),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )
            if self._access is not None:
                owner = self.read(value.investigation_id)
                if owner is None or owner.project_id != value.project_id:
                    raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
                self._seal(
                    value.project_id, value.event_digest, (f"control:{owner.investigation_digest}",)
                )

    def list_audit(
        self, project_id: str, investigation_id: str, *, offset: int, limit: int
    ) -> tuple[InvestigationAuditRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(investigation_audit)
                .where(
                    investigation_audit.c.project_id == project_id,
                    investigation_audit.c.investigation_id == investigation_id,
                )
                .order_by(
                    investigation_audit.c.created_at,
                    investigation_audit.c.audit_id,
                )
                .offset(offset)
                .limit(limit)
            ).mappings()
            values = tuple(
                InvestigationAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    investigation_id=str(row["investigation_id"]),
                    event_type=str(row["event_type"]),
                    payload=_object(str(row["payload_json"])),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )
        return tuple(value for value in values if self._visible(project_id, value.event_digest))

    @staticmethod
    def _values(value: InvestigationRecord) -> dict[str, object]:
        return {
            "investigation_id": value.investigation_id,
            "project_id": value.project_id,
            "thread_id": value.thread_id,
            "cycle_id": value.cycle_id,
            "parent_investigation_id": value.parent_investigation_id,
            "trigger": value.trigger,
            "question": value.question,
            "target_object_id": value.target_object_id,
            "target_hypothesis_id": value.target_hypothesis_id,
            "mode": value.mode,
            "scope_json": orjson.dumps(value.scope, option=orjson.OPT_SORT_KEYS).decode(),
            "required_evidence_groups_json": orjson.dumps(value.required_evidence_groups).decode(),
            "query_families_json": orjson.dumps(value.query_families).decode(),
            "counter_search_policy": value.counter_search_policy,
            "budget": value.budget,
            "budget_usage": value.budget_usage,
            "stop_conditions_json": orjson.dumps(value.stop_conditions).decode(),
            "domain_state": value.domain_state,
            "execution_state": value.execution_state,
            "current_wave": value.current_wave,
            "observation_count": value.observation_count,
            "open_lead_count": value.open_lead_count,
            "claim_candidate_count": value.claim_candidate_count,
            "gap_count": value.gap_count,
            "sufficiency_json": orjson.dumps(
                value.sufficiency, option=orjson.OPT_SORT_KEYS
            ).decode(),
            "checkpoint_digest": value.checkpoint_digest,
            "result_json": (
                None
                if value.result is None
                else orjson.dumps(value.result, option=orjson.OPT_SORT_KEYS).decode()
            ),
            "plan_revision": value.plan_revision,
            "investigation_digest": value.investigation_digest,
            "created_at": value.created_at.isoformat(),
            "updated_at": value.updated_at.isoformat(),
        }

    @staticmethod
    def _record(row: object) -> InvestigationRecord:
        value = cast(dict[str, object], row)
        return InvestigationRecord(
            investigation_id=str(value["investigation_id"]),
            project_id=str(value["project_id"]),
            thread_id=str(value["thread_id"]),
            cycle_id=str(value["cycle_id"]),
            parent_investigation_id=(
                None
                if value["parent_investigation_id"] is None
                else str(value["parent_investigation_id"])
            ),
            trigger=str(value["trigger"]),
            question=str(value["question"]),
            target_object_id=(
                None if value["target_object_id"] is None else str(value["target_object_id"])
            ),
            target_hypothesis_id=(
                None
                if value["target_hypothesis_id"] is None
                else str(value["target_hypothesis_id"])
            ),
            mode=str(value["mode"]),
            scope={
                str(key): str(child) for key, child in _object(str(value["scope_json"])).items()
            },
            required_evidence_groups=_tuple(str(value["required_evidence_groups_json"])),
            query_families=_tuple(str(value["query_families_json"])),
            counter_search_policy=str(value["counter_search_policy"]),
            budget=int(str(value["budget"])),
            budget_usage=int(str(value["budget_usage"])),
            stop_conditions=_tuple(str(value["stop_conditions_json"])),
            domain_state=str(value["domain_state"]),
            execution_state=str(value["execution_state"]),
            current_wave=int(str(value["current_wave"])),
            observation_count=int(str(value["observation_count"])),
            open_lead_count=int(str(value["open_lead_count"])),
            claim_candidate_count=int(str(value["claim_candidate_count"])),
            gap_count=int(str(value["gap_count"])),
            sufficiency=_object(str(value["sufficiency_json"])),
            checkpoint_digest=(
                None if value["checkpoint_digest"] is None else str(value["checkpoint_digest"])
            ),
            result=(None if value["result_json"] is None else _object(str(value["result_json"]))),
            plan_revision=int(str(value["plan_revision"])),
            investigation_digest=str(value["investigation_digest"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )
