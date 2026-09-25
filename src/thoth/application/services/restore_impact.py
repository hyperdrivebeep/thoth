"""Typed current consumers plus exact result bases; historical edges are not authority."""

from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError

from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType
from thoth.domain.relation import DependencyRelation
from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.restore import RestoreError
from thoth.domain.revision import ImpactPropagationPlan, SemanticRevision
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.restore import RestoreProfileRegistryPort


@dataclass(frozen=True)
class RestoreImpact:
    plan: ImpactPropagationPlan
    checked_heads: dict[str, str]
    consumer_basis: dict[str, object]


class RestoreImpactPlanner:
    def __init__(
        self,
        ledger: LedgerPort,
        dependencies: DependencyGraphPort,
        profiles: RestoreProfileRegistryPort,
        access: ResourceAccessPort,
        read_owners: tuple[tuple[EntityType, type[DomainModel], str], ...] = (),
    ) -> None:
        self.ledger, self.dependencies, self.profiles, self.access = (
            ledger,
            dependencies,
            profiles,
            access,
        )
        self.read_owners = read_owners

    def build(self, project: str, target_key: str, object_id: str) -> RestoreImpact:
        heads = dict(self.ledger.read_heads(project))
        if len(heads) > 2000:
            raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
        consumers: dict[str, set[str]] = {}
        same_object: set[str] = set()
        for key, digest in heads.items():
            if key == f"DECISION_OBJECT:{object_id}":
                continue
            revision = self.ledger.read_revision_by_digest(project, digest)
            snapshot = None if revision is None else self.ledger.read_snapshot(revision.snapshot_id)
            if revision is None or snapshot is None:
                raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
            content = snapshot.content
            raw_basis = content.get("research_basis")
            if raw_basis is not None:
                basis = ResearchResultBasis.model_validate(raw_basis)
                for dependency in basis.effective_expected_heads:
                    consumers.setdefault(dependency, set()).add(key)
                continue
            if content.get("object_id", content.get("target_object_id")) != object_id:
                # Old result's object is a related-object hint, never exact USED_BY.
                result = content.get("result")
                plan = (
                    cast(dict[str, object], result).get("action_plan")
                    if isinstance(result, dict)
                    else None
                )
                if (
                    isinstance(plan, dict)
                    and cast(dict[str, object], plan).get("object_id") == object_id
                ):
                    same_object.add(key)
                continue
            same_object.add(key)
            if content.get("record_kind") is not None:
                from thoth.domain.research_codec import decode_research_record

                try:
                    decode_research_record(dict(content))
                except ValueError as exc:
                    raise RestoreError("RESTORE_IMPACT_INCOMPLETE") from exc
                continue
            try:
                profile, record = self.profiles.resolve(revision, snapshot)
            except RestoreError as exc:
                if exc.reason_code != "RESTORE_SCHEMA_UNSUPPORTED":
                    raise
                # Known non-restorable owners are historical consumers only.
                from thoth.domain.research_identity import ResearchFamily, decode_research

                if (
                    decode_research(revision, snapshot).identity.schema_family
                    == ResearchFamily.UNRESOLVED
                ) and not self._read_owner_matches(revision, dict(content)):
                    raise RestoreError("RESTORE_IMPACT_INCOMPLETE") from exc
                continue
            for dependency in profile.references(record):
                consumers.setdefault(dependency, set()).add(key)
        active_edges: list[DependencyRelation] = []
        for relation in self.dependencies.read_relations(project):
            if relation.revision_digest not in {
                heads.get(relation.source_ref),
                heads.get(relation.target_ref),
            }:
                continue
            consumers.setdefault(relation.source_ref, set()).add(relation.target_ref)
            active_edges.append(relation)
        reached: set[str] = set()
        frontier = list(consumers.get(target_key, ()))
        while frontier:
            key = frontier.pop()
            if key == target_key:
                raise RestoreError("RESTORE_DEPENDENCY_CYCLE")
            if key in reached:
                continue
            reached.add(key)
            frontier.extend(consumers.get(key, ()))
        # Existing object-level records lack complete producer bases: conservatively review
        # related mutable consumers without claiming exact causal association.
        reached.update(same_object - {target_key})
        checked = {target_key, *reached}
        for key in checked:
            if key not in heads:
                raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
            if not self.access.may_read_revision(project, heads[key]):
                raise RestoreError("RESTORE_DEPENDENT_SCOPE_DENIED")
        return RestoreImpact(
            ImpactPropagationPlan(
                stale_refs=(target_key,), recalculate_refs=tuple(sorted(reached))
            ),
            {key: heads[key] for key in sorted(checked)},
            {
                "relations": [
                    edge.model_dump(mode="json")
                    for edge in active_edges
                    if edge.source_ref in checked or edge.target_ref in checked
                ],
                "related_object_refs": sorted(same_object - {target_key}),
            },
        )

    def _read_owner_matches(self, revision: SemanticRevision, content: dict[str, object]) -> bool:
        matches = 0
        for kind, codec, identifier in self.read_owners:
            if revision.entity_type != kind:
                continue
            try:
                owner = codec.model_validate(content).model_dump(mode="python")
            except ValidationError:
                continue
            if (
                owner.get("project_id") == revision.project_id
                and owner.get(identifier) == revision.entity_id
                and owner.get("revision_digest") == revision.revision_digest
            ):
                matches += 1
        return matches == 1
