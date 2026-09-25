"""Registered full-schema restore rules and deterministic semantic diff groups."""

from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.research_history import SemanticChangeGroup
from thoth.domain.restore import RestoreError
from thoth.domain.revision import EntitySnapshot, SemanticDiffEntry, SemanticRevision
from thoth.ports.restore import RestoreProfilePort


@dataclass(frozen=True)
class TypedRestoreProfile:
    profile_id: str
    codec: type[DomainModel]
    entity_type: EntityType
    identifier_field: str
    object_field: str = "object_id"
    reference_fields: tuple[tuple[str, str], ...] = ()
    display_name: str = "연구 항목"

    def decode(self, revision: SemanticRevision, snapshot: EntitySnapshot) -> DomainModel | None:
        if revision.entity_type != self.entity_type or snapshot.schema_version not in {
            "1.0.0",
            "1.1.0",
        }:
            return None
        try:
            record = self.codec.model_validate(snapshot.content)
        except ValidationError:
            return None
        values = record.model_dump(mode="python")
        if values.get("schema_version") not in {"1.0.0", "1.1.0"}:
            return None
        if (
            values.get(self.identifier_field) != revision.entity_id
            or values.get("project_id") != revision.project_id
        ):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        if snapshot.content_digest not in {
            domain_digest(kind, "1.0.0", canonical_payload(snapshot.content))
            for kind in ("SNAPSHOT", "ENTITY_SNAPSHOT")
        }:
            raise RestoreError("RESTORE_CONTENT_DIGEST_MISMATCH")
        return record

    def object_id(self, record: DomainModel) -> str:
        return str(record.model_dump()[self.object_field])

    def references(self, record: DomainModel) -> tuple[str, ...]:
        values = record.model_dump(mode="python")
        return tuple(
            f"{kind}:{identifier}"
            for field, kind in self.reference_fields
            for identifier in values.get(field, ())
        )

    def evidence_refs(self, record: DomainModel) -> tuple[str, ...]:
        def collect(value: object) -> set[str]:
            refs: set[str] = set()
            if isinstance(value, dict):
                for key, nested in cast(dict[str, object], value).items():
                    if key in {"evidence_refs", "counterevidence_refs"} and isinstance(
                        nested, (tuple, list)
                    ):
                        refs.update(
                            str(ref) for ref in cast(list[object] | tuple[object, ...], nested)
                        )
                    else:
                        refs.update(collect(nested))
            elif isinstance(value, (tuple, list)):
                for nested in cast(list[object] | tuple[object, ...], value):
                    refs.update(collect(nested))
            return refs

        return tuple(sorted(collect(record.model_dump(mode="python"))))


class RestoreProfileRegistry:
    def __init__(self, profiles: tuple[RestoreProfilePort, ...]) -> None:
        self.profiles = profiles
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise ValueError("RESTORE_PROFILE_DUPLICATE")

    def resolve(
        self, revision: SemanticRevision, snapshot: EntitySnapshot
    ) -> tuple[RestoreProfilePort, DomainModel]:
        if (
            revision.project_id,
            revision.entity_type,
            revision.entity_id,
            revision.snapshot_id,
        ) != (snapshot.project_id, snapshot.entity_type, snapshot.entity_id, snapshot.snapshot_id):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        matches = [
            (profile, record)
            for profile in self.profiles
            if (record := profile.decode(revision, snapshot)) is not None
        ]
        if len(matches) != 1:
            raise RestoreError("RESTORE_SCHEMA_UNSUPPORTED")
        return matches[0]


def semantic_groups(changes: tuple[SemanticDiffEntry, ...]) -> tuple[SemanticChangeGroup, ...]:
    fields = {
        "CONTENT": {"statement", "observed_problem", "specification", "decision_question", "steps"},
        "EVIDENCE": {
            "evidence_refs",
            "counterevidence_refs",
            "evidence_basis",
            "evidence_coverage",
        },
        "CONDITION": {
            "scope",
            "cutoff_at",
            "policy_version",
            "mandatory_criteria",
            "hypothesis_refs",
            "action_refs",
        },
        "STATUS": {
            "empirical_appraisal",
            "freshness",
            "decision_state",
            "validation_state",
            "derived_status",
        },
        "IMPACT": {"effect_vector", "impact_set", "cumulative_impact", "risk_tier"},
    }
    grouped: dict[str, list[SemanticDiffEntry]] = {}
    for change in changes:
        field = change.path.split("/")[1] if "/" in change.path else ""
        kind = next((key for key, known in fields.items() if field in known), "OTHER")
        grouped.setdefault(kind, []).append(change)
    return tuple(
        SemanticChangeGroup.model_validate(
            {
                "kind": kind,
                "trace_paths": tuple(change.path for change in values),
                "changes": tuple(values),
                "summary": "기술 변경 · 의미 요약 미확인"
                if kind == "OTHER"
                else f"{len(values)}개 항목 변경",
            }
        )
        for kind, values in grouped.items()
    )
