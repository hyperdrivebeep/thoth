from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, Integer, insert, literal_column, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.research_identity import canonical_records
from thoth.adapters.storage.schema import (
    criterion_audit,
    criterion_conflicts,
    criterion_contracts,
    criterion_profiles,
    criterion_references,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.criterion_contract import (
    CriterionAuditRecord,
    CriterionConflictRecord,
    CriterionContractRecord,
    CriterionProfileRecord,
    CriterionReferenceCandidate,
)
from thoth.domain.enums import EntityType
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.resource_scope import ResourceAccessPort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored criterion payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteCriterionContractStore(CriterionContractStorePort):
    def __init__(
        self,
        engine: Engine,
        ledger: LedgerPort | None = None,
        resource_access: ResourceAccessPort | None = None,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._resource_access = resource_access

    def put_profile(self, value: CriterionProfileRecord) -> None:
        payload = {
            "profile_ref": value.profile_ref,
            "version": value.version,
            "name": value.name,
            "domain_hint": value.domain_hint,
            "required_fields_json": dump(value.required_fields),
            "conditional_fields_json": dump(value.conditional_fields),
            "allowed_computation_types_json": dump(value.allowed_computation_types),
            "authority_policy_json": dump(value.authority_policy),
            "enabled": int(value.enabled),
            "profile_digest": value.profile_digest,
            "profile_json": dump(value.model_dump(mode="json")),
        }
        statement = sqlite_insert(criterion_profiles).values(**payload)
        with write_connection(self._engine) as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["profile_ref", "version"],
                    set_={
                        key: child
                        for key, child in payload.items()
                        if key not in {"profile_ref", "version"}
                    },
                )
            )

    def list_profiles(self, enabled_only: bool) -> tuple[CriterionProfileRecord, ...]:
        statement = select(criterion_profiles)
        if enabled_only:
            statement = statement.where(criterion_profiles.c.enabled == 1)
        with read_connection(self._engine) as connection:
            rows = tuple(
                connection.execute(
                    statement.order_by(
                        criterion_profiles.c.profile_ref, criterion_profiles.c.version
                    )
                ).mappings()
            )
        latest: dict[str, CriterionProfileRecord] = {}
        for row in rows:
            profile = self._profile(row)
            latest[profile.profile_ref] = profile
        return tuple(latest[key] for key in sorted(latest))

    def read_profile(self, profile_ref: str, version: int | None) -> CriterionProfileRecord | None:
        statement = select(criterion_profiles).where(
            criterion_profiles.c.profile_ref == profile_ref
        )
        if version is not None:
            statement = statement.where(criterion_profiles.c.version == version)
        else:
            statement = statement.order_by(criterion_profiles.c.version.desc()).limit(1)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else self._profile(row)

    def add_contract(self, value: CriterionContractRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(criterion_contracts).values(
                    criterion_revision_id=value.criterion_revision_id,
                    criterion_id=value.criterion_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    content_json=dump(value.model_dump(mode="json")),
                    revision_digest=value.revision_digest,
                    supersedes_revision_digest=value.supersedes_revision_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_contracts(self, project_id: str) -> tuple[CriterionContractRecord, ...]:
        if self._ledger is not None:
            return canonical_records(
                self._ledger,
                self._engine,
                EntityType.CRITERION,
                project_id,
                criterion_contracts,
                "criterion_id",
                CriterionContractRecord,
                resource_access=self._resource_access,
            )
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(criterion_contracts)
                .where(criterion_contracts.c.project_id == project_id)
                .order_by(criterion_contracts.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            return self._latest(tuple(self._contract(row) for row in rows))

    def read_contract(
        self, project_id: str, criterion_id: str, revision_digest: str | None
    ) -> CriterionContractRecord | None:
        if self._ledger is not None:
            records = canonical_records(
                self._ledger,
                self._engine,
                EntityType.CRITERION,
                project_id,
                criterion_contracts,
                "criterion_id",
                CriterionContractRecord,
                identifier=criterion_id,
                revision_digest=revision_digest,
                resource_access=self._resource_access,
            )
            return records[0] if records else None
        statement = select(criterion_contracts).where(
            criterion_contracts.c.project_id == project_id,
            criterion_contracts.c.criterion_id == criterion_id,
        )
        if revision_digest is not None:
            statement = statement.where(criterion_contracts.c.revision_digest == revision_digest)
        else:
            statement = statement.order_by(
                criterion_contracts.c.created_at.desc(), literal_column("rowid", Integer()).desc()
            ).limit(1)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else self._contract(row)

    def add_reference(self, value: CriterionReferenceCandidate) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(criterion_references).values(
                    reference_candidate_id=value.reference_candidate_id,
                    project_id=value.project_id,
                    criterion_id=value.criterion_id,
                    content_json=dump(value.model_dump(mode="json")),
                    reference_digest=value.reference_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_references(
        self, project_id: str, criterion_id: str | None
    ) -> tuple[CriterionReferenceCandidate, ...]:
        statement = select(criterion_references).where(
            criterion_references.c.project_id == project_id
        )
        if criterion_id is not None:
            statement = statement.where(criterion_references.c.criterion_id == criterion_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(criterion_references.c.created_at)
            ).mappings()
            values = tuple(
                CriterionReferenceCandidate.model_validate(load(row["content_json"]))
                for row in rows
            )
        return tuple(value for value in values if self._may_read_auxiliary(value))

    def read_reference(self, reference_candidate_id: str) -> CriterionReferenceCandidate | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(criterion_references).where(
                        criterion_references.c.reference_candidate_id == reference_candidate_id
                    )
                )
                .mappings()
                .first()
            )
        value = (
            None
            if row is None
            else CriterionReferenceCandidate.model_validate(load(row["content_json"]))
        )
        if value is not None:
            self._require_auxiliary(value)
        return value

    def add_conflict(self, value: CriterionConflictRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(criterion_conflicts).values(
                    conflict_id=value.conflict_id,
                    project_id=value.project_id,
                    criterion_id=value.criterion_id,
                    field_path=value.field_path,
                    candidate_values_json=dump(value.candidate_values),
                    evidence_refs_json=dump(value.evidence_refs),
                    status=value.status,
                    impact_json=dump(value.impact),
                    conflict_digest=value.conflict_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_conflicts(
        self, project_id: str, criterion_id: str | None
    ) -> tuple[CriterionConflictRecord, ...]:
        statement = select(criterion_conflicts).where(
            criterion_conflicts.c.project_id == project_id
        )
        if criterion_id is not None:
            statement = statement.where(criterion_conflicts.c.criterion_id == criterion_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(criterion_conflicts.c.created_at)
            ).mappings()
            values = tuple(self._conflict(row) for row in rows)
        return tuple(value for value in values if self._may_read_auxiliary(value))

    def read_conflict(self, conflict_id: str) -> CriterionConflictRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(criterion_conflicts).where(
                        criterion_conflicts.c.conflict_id == conflict_id
                    )
                )
                .mappings()
                .first()
            )
        value = None if row is None else self._conflict(row)
        if value is not None:
            self._require_auxiliary(value)
        return value

    def append_audit(self, value: CriterionAuditRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(criterion_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    criterion_id=value.criterion_id,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_audit(self, project_id: str, criterion_id: str) -> tuple[CriterionAuditRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(criterion_audit)
                .where(
                    criterion_audit.c.project_id == project_id,
                    criterion_audit.c.criterion_id == criterion_id,
                )
                .order_by(criterion_audit.c.created_at)
            ).mappings()
            return tuple(
                CriterionAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    criterion_id=str(row["criterion_id"]),
                    event_type=str(row["event_type"]),
                    payload=load(row["payload_json"]),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    @staticmethod
    def _profile(row: object) -> CriterionProfileRecord:
        value = cast(dict[str, object], row)
        profile_json = value.get("profile_json")
        if profile_json not in {None, ""}:
            return CriterionProfileRecord.model_validate(load(profile_json))
        return CriterionProfileRecord(
            profile_ref=str(value["profile_ref"]),
            version=int(str(value["version"])),
            name=str(value["name"]),
            domain_hint=str(value["domain_hint"]),
            required_fields=tuple(
                str(item)
                for item in cast(list[object], orjson.loads(str(value["required_fields_json"])))
            ),
            conditional_fields=tuple(
                str(item)
                for item in cast(list[object], orjson.loads(str(value["conditional_fields_json"])))
            ),
            allowed_computation_types=tuple(
                str(item)
                for item in cast(
                    list[object], orjson.loads(str(value["allowed_computation_types_json"]))
                )
            ),
            authority_policy={
                str(k): str(v) for k, v in load(value["authority_policy_json"]).items()
            },
            enabled=bool(value["enabled"]),
            profile_digest=str(value["profile_digest"]),
        )

    def _require_auxiliary(
        self, value: CriterionReferenceCandidate | CriterionConflictRecord
    ) -> None:
        if self._resource_access is None:
            return
        contract = self.read_contract(value.project_id, value.criterion_id, None)
        if contract is None:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        refs = (
            value.source_lineage
            if isinstance(value, CriterionReferenceCandidate)
            else value.evidence_refs
        )
        self._resource_access.require_reads(value.project_id, refs)

    def _may_read_auxiliary(
        self, value: CriterionReferenceCandidate | CriterionConflictRecord
    ) -> bool:
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
    def _contract(row: object) -> CriterionContractRecord:
        return CriterionContractRecord.model_validate(
            load(cast(dict[str, object], row)["content_json"])
        )

    @staticmethod
    def _conflict(row: object) -> CriterionConflictRecord:
        value = cast(dict[str, object], row)
        return CriterionConflictRecord(
            conflict_id=str(value["conflict_id"]),
            project_id=str(value["project_id"]),
            criterion_id=str(value["criterion_id"]),
            field_path=str(value["field_path"]),
            candidate_values=tuple(
                cast(list[object], orjson.loads(str(value["candidate_values_json"])))
            ),
            evidence_refs=tuple(
                str(item)
                for item in cast(list[object], orjson.loads(str(value["evidence_refs_json"])))
            ),
            status=str(value["status"]),
            impact=load(value["impact_json"]),
            conflict_digest=str(value["conflict_digest"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )

    @staticmethod
    def _latest(values: tuple[CriterionContractRecord, ...]) -> tuple[CriterionContractRecord, ...]:
        latest: dict[str, CriterionContractRecord] = {}
        for value in values:
            latest[value.criterion_id] = value
        return tuple(latest[key] for key in sorted(latest))
