from __future__ import annotations

import json
import re
from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from typing import cast

from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import (
    CriterionAuditRecord,
    CriterionConflictRecord,
    CriterionContractRecord,
    CriterionProfileRecord,
    CriterionReferenceCandidate,
)
from thoth.domain.enums import ActorKind, AuthorityState, EntityType
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.reference import (
    ReferenceInquiry,
    criterion_reference_basis,
    evaluate_reference_formula,
    seal_inquiry,
)
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.criterion_profile import CriterionProfileCatalogPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

TARGET_PATTERNS = (
    re.compile(
        r"(?P<operator>below|less than|under|at most|<=|<)\s*"
        r"(?P<target>\d+(?:\.\d+)?)\s*(?P<unit>%|ms|s|kg|g|m|km|[A-Za-z0-9/²]+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<operator>at least|no less than|>=|>)\s*"
        r"(?P<target>\d+(?:\.\d+)?)\s*(?P<unit>%|ms|s|kg|g|m|km|[A-Za-z0-9/²]+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<target>\d+(?:\.\d+)?)\s*(?P<unit>%|ms|s)?\s*"
        r"(?P<operator>이상|이하|미만|초과)"
    ),
)


class CriterionContractService:
    def __init__(
        self,
        *,
        store: CriterionContractStorePort,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        profile_catalog: CriterionProfileCatalogPort,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._ledger = ledger
        self._commits = commits
        self._clock = clock
        self._ids = ids
        self._profile_catalog = profile_catalog

    @contextmanager
    def transaction(self, current: CriterionContractRecord) -> Generator[None]:
        """Bind a short Criterion mutation to its current canonical head and projection."""
        with self._ledger.transaction() as transaction:
            latest = self._store.read_contract(current.project_id, current.criterion_id, None)
            head = transaction.get_head(current.project_id, f"CRITERION:{current.criterion_id}")
            if (
                latest is None
                or latest.revision_digest != current.revision_digest
                or head != current.revision_digest
            ):
                raise ValueError("CRITERION_REVISION_CONFLICT")
            yield

    def seed_profiles(self) -> None:
        profiles = self._profile_catalog.profiles()
        with self._ledger.transaction():
            for profile in profiles:
                self._store.put_profile(profile)

    def compile(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        source_span_ids: tuple[str, ...],
        goal_requirement_refs: tuple[str, ...],
        profile_refs: tuple[str, ...],
    ) -> tuple[CriterionContractRecord, CommitResult]:
        spans = self._spans(project_id, source_span_ids)
        if not spans:
            raise ValueError("criterion compilation requires source spans")
        profiles = self._profiles(profile_refs or ("GENERAL_RND",))
        combined = "\n".join(span.exact_text for span in spans)
        rule = self._acceptance_rule(combined)
        computation = self._computation(combined, rule)
        construct = self._construct(combined)
        context = {"source_context": combined[:2_000]}
        field_map: dict[str, tuple[str, ...]] = {
            "construct_outcome_definition": source_span_ids,
            "verification_spec": source_span_ids,
            "context_spec": source_span_ids,
        }
        if rule is not None:
            field_map["acceptance_rule"] = source_span_ids
        if computation is not None:
            field_map["computation_spec"] = source_span_ids
        required = tuple(
            dict.fromkeys(field for profile in profiles for field in profile.required_fields)
        )
        missing = tuple(
            field
            for field in required
            if {
                "construct": bool(construct),
                "verification_spec": True,
                "context_spec": bool(context),
                "acceptance_rule": rule is not None,
                "uncertainty": False,
                "prespecification": False,
                "evaluator_binding": False,
            }.get(field, False)
            is False
        )
        criterion_id = self._ids.new("criterion")
        draft: dict[str, object] = {
            "criterion_revision_id": self._ids.new("criterion-revision"),
            "criterion_id": criterion_id,
            "project_id": project_id,
            "thread_id": thread_id,
            "identity": {"name": construct[:160] or "Unresolved criterion"},
            "profile_refs": tuple(profile.profile_ref for profile in profiles),
            "goal_requirement_refs": goal_requirement_refs,
            "construct_outcome_definition": construct,
            "verification_spec": {
                "method": "SOURCE_GROUNDED_CANDIDATE",
                "level": "UNRESOLVED",
                "phase": "CURRENT_PROJECT_PHASE",
                "responsible_role": "EVIDENCE_EVALS_OWNER",
            },
            "computation_spec": computation,
            "context_spec": context,
            "acceptance_rule": rule,
            "required_evidence": source_span_ids,
            "field_evidence_map": field_map,
            "field_authority_and_version": {
                field: {
                    "authority": self._authority(spans).value,
                    "version": "SOURCE_VERSION_BOUND",
                    "applicability": "CANDIDATE",
                }
                for field in field_map
            },
            "governance": {
                "prespecification": "UNKNOWN",
                "target_rationale": "EXTRACTED_FROM_CONTROLLING_SOURCE_CANDIDATE",
                "deviations": (),
                "change_authority": "CRITERION_OWNER_R3",
            },
            "lifecycle": "COMPILED",
            "completeness": "COMPLETE" if not missing else "INCOMPLETE",
            "consistency": "CONSISTENT",
            "comparability": "NOT_ASSESSED",
            "technical_executability": (
                "EXECUTABLE_CANDIDATE" if computation is not None else "NOT_EXECUTABLE"
            ),
            "measurement_validity": "NOT_ASSESSED",
            "usage_authorization": "NOT_AUTHORIZED",
            "prespecification": "UNKNOWN",
            "missing_fields": missing,
            "created_at": self._clock.now(),
        }
        contract = CriterionContractRecord.model_validate(
            {**draft, "revision_digest": self._digest(draft)}
        )
        return self._persist(contract, "criteria/compiled")

    def revise(
        self,
        current: CriterionContractRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        actor: ActorRef | None = None,
    ) -> tuple[CriterionContractRecord, CommitResult]:
        draft = current.model_dump(mode="python")
        draft.update(updates)
        draft.update(
            {
                "criterion_revision_id": self._ids.new("criterion-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        draft.pop("receipt_ref", None)
        inquiry = draft.get("reference_inquiry")
        if inquiry is not None:
            inquiry = ReferenceInquiry.model_validate(inquiry)
            if inquiry.criterion_basis_digest != criterion_reference_basis(draft):
                draft["reference_inquiry"] = seal_inquiry(
                    {
                        **inquiry.model_dump(mode="python", exclude={"inquiry_digest"}),
                        "state": "STALE",
                        "calculation": None,
                        "scenario_results": (),
                        "reason_codes": ("REFERENCE_CRITERION_BASIS_CHANGED",),
                        "updated_at": draft["created_at"],
                    }
                )
        revised = CriterionContractRecord.model_validate(
            {**draft, "revision_digest": self._digest(draft)}
        )
        return self._persist(revised, event_type, actor=actor)

    def revalidate(
        self, current: CriterionContractRecord
    ) -> tuple[CriterionContractRecord, CommitResult]:
        missing = current.missing_fields
        complete = "COMPLETE" if not missing else "INCOMPLETE"
        computation_type = (
            None if current.computation_spec is None else current.computation_spec.get("type")
        )
        deterministic = (
            computation_type == "FORMULA"
            and current.computation_spec is not None
            and isinstance(current.computation_spec.get("expression"), str)
        )
        all_official = all(
            value.get("authority") in {"OFFICIAL", "APPROVED"}
            for value in current.field_authority_and_version.values()
        )
        return self.revise(
            current,
            updates={
                "completeness": complete,
                "technical_executability": (
                    "DETERMINISTICALLY_EXECUTABLE"
                    if deterministic
                    else current.technical_executability
                ),
                "usage_authorization": (
                    "AUTHORIZED_EVALUATOR_INPUT"
                    if complete == "COMPLETE"
                    and deterministic
                    and all_official
                    and current.consistency == "CONSISTENT"
                    else "NOT_AUTHORIZED"
                ),
            },
            event_type="criteria/readinessUpdated",
        )

    def recalculate(
        self,
        current: CriterionContractRecord,
        *,
        variables: dict[str, Decimal],
        evaluator_binding_digest: str,
    ) -> tuple[CriterionContractRecord, CommitResult]:
        if current.usage_authorization != "AUTHORIZED_EVALUATOR_INPUT":
            raise ValueError("criterion is not authorized evaluator input")
        computation = current.computation_spec or {}
        if computation.get("type") != "FORMULA":
            raise ValueError("only approved FORMULA can be recalculated in Criteria")
        expected_digest = computation.get("evaluator_binding_digest")
        if expected_digest is not None and expected_digest != evaluator_binding_digest:
            raise ValueError("evaluator binding digest mismatch")
        expression = computation.get("expression")
        if not isinstance(expression, str):
            raise ValueError("formula expression is missing")
        result = evaluate_formula(expression, variables)
        return self.revise(
            current,
            updates={
                "result_and_uncertainty": {
                    "value": str(result),
                    "variables": {key: str(value) for key, value in variables.items()},
                    "evaluator_binding_digest": evaluator_binding_digest,
                    "measurement_validity": "NOT_ASSESSED",
                    "disposition": "NOT_DECIDED",
                }
            },
            event_type="criteria/recalculated",
        )

    def reference_candidate(
        self,
        current: CriterionContractRecord,
        *,
        source_scope: tuple[str, ...],
        scenarios: tuple[str, ...],
    ) -> CriterionReferenceCandidate:
        draft: dict[str, object] = {
            "reference_candidate_id": self._ids.new("criterion-reference"),
            "project_id": current.project_id,
            "criterion_id": current.criterion_id,
            "source_lineage": source_scope,
            "normalization": "NO_SILENT_NORMALIZATION",
            "uncertainty": "Reference range requires domain and authority review",
            "scenarios": scenarios,
            "applicability_gaps": ("authority", "population/context", "method equivalence"),
            "assumptions": ("source mapping is provisional",),
            "authorization_state": "NOT_AUTHORIZED",
            "evaluator_input_allowed": False,
            "created_at": self._clock.now(),
        }
        candidate = CriterionReferenceCandidate.model_validate(
            {
                **draft,
                "reference_digest": domain_digest(
                    "CRITERION_REFERENCE", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        with self.transaction(current):
            self._store.add_reference(candidate)
            self.audit(current, "criteria/referenceUpdated", draft)
        return candidate

    def evidence_authority(self, project_id: str, span_ids: tuple[str, ...]) -> AuthorityState:
        """Derive field authority only from the cited in-project source spans."""
        if not span_ids:
            return AuthorityState.UNCLASSIFIED
        return self._authority(self._spans(project_id, span_ids))

    def conflict(
        self,
        current: CriterionContractRecord,
        *,
        field_path: str,
        candidate_values: tuple[object, ...],
        evidence_refs: tuple[str, ...],
    ) -> CriterionConflictRecord:
        draft: dict[str, object] = {
            "conflict_id": self._ids.new("criterion-conflict"),
            "project_id": current.project_id,
            "criterion_id": current.criterion_id,
            "field_path": field_path,
            "candidate_values": candidate_values,
            "evidence_refs": evidence_refs,
            "status": "OPEN",
            "impact": {"dependent_state": "STALE", "resolution": "REVISION_OR_AUTHORITY_REQUIRED"},
            "created_at": self._clock.now(),
        }
        conflict = CriterionConflictRecord.model_validate(
            {
                **draft,
                "conflict_digest": domain_digest(
                    "CRITERION_CONFLICT", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        with self.transaction(current):
            self._store.add_conflict(conflict)
            self.audit(current, "criteria/conflictUpdated", draft)
        return conflict

    def audit(
        self, current: CriterionContractRecord, event_type: str, payload: dict[str, object]
    ) -> CriterionAuditRecord:
        created_at = self._clock.now()
        # Preserve nested typed values in the same representation after JSON storage.
        payload = cast(dict[str, object], json.loads(canonical_payload(payload)))
        draft = {
            "project_id": current.project_id,
            "criterion_id": current.criterion_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
        }
        record = CriterionAuditRecord(
            audit_id=self._ids.new("criterion-audit"),
            project_id=current.project_id,
            criterion_id=current.criterion_id,
            event_type=event_type,
            payload=payload,
            event_digest=domain_digest("CRITERION_AUDIT", "1.0.0", canonical_payload(draft)),
            created_at=created_at,
        )
        with self.transaction(current):
            self._store.append_audit(record)
        return record

    def _persist(
        self, contract: CriterionContractRecord, event_type: str, *, actor: ActorRef | None = None
    ) -> tuple[CriterionContractRecord, CommitResult]:
        content = contract.model_dump(mode="python")
        snapshot_digest = domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content))
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=contract.project_id,
            entity_type=EntityType.CRITERION,
            entity_id=contract.criterion_id,
            schema_version="1.0.0",
            content=content,
            content_digest=snapshot_digest,
        )
        parents = (
            ()
            if contract.supersedes_revision_digest is None
            else (contract.supersedes_revision_digest,)
        )
        revision = SemanticRevision(
            revision_id=contract.criterion_revision_id,
            project_id=contract.project_id,
            entity_type=EntityType.CRITERION,
            entity_id=contract.criterion_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=parents,
            actor=actor
            or ActorRef(
                actor_id="agent:criterion-compiler",
                kind=ActorKind.AGENT,
                role="criterion-compiler",
            ),
            reason=event_type,
            evidence_refs=tuple(contract.required_evidence),
            affected_refs=(),
            revision_digest=contract.revision_digest,
            created_at=contract.created_at,
        )
        expected = (
            {}
            if contract.supersedes_revision_digest is None
            else {f"CRITERION:{contract.criterion_id}": contract.supersedes_revision_digest}
        )
        with self._ledger.transaction() as transaction:
            latest = self._store.read_contract(contract.project_id, contract.criterion_id, None)
            head = transaction.get_head(contract.project_id, f"CRITERION:{contract.criterion_id}")
            projected = None if latest is None else latest.revision_digest
            if (
                head != contract.supersedes_revision_digest
                or projected != contract.supersedes_revision_digest
            ):
                raise ValueError("CRITERION_REVISION_CONFLICT")
            commit = self._commits.commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=contract.project_id,
                    expected_heads=expected,
                    staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                    impact_plan=ImpactPropagationPlan(),
                    actor=revision.actor,
                    reason=event_type,
                )
            )
            if not commit.committed_revision_ids:
                raise ValueError("criterion commit branched because expected revision changed")
            self._store.add_contract(contract)
            self.audit(contract, event_type, {"revision_digest": contract.revision_digest})
        return contract, commit

    def _spans(self, project_id: str, span_ids: tuple[str, ...]) -> tuple[EvidenceSpan, ...]:
        spans: list[EvidenceSpan] = []
        for span_id in span_ids:
            span = self._artifacts.read_evidence(span_id)
            if span is None or span.project_id != project_id:
                raise ValueError("criterion source span was not found in this project")
            spans.append(span)
        return tuple(spans)

    def _profiles(self, profile_refs: tuple[str, ...]) -> tuple[CriterionProfileRecord, ...]:
        profiles: list[CriterionProfileRecord] = []
        for profile_ref in profile_refs:
            profile = self._store.read_profile(profile_ref, None)
            if profile is None or not profile.enabled:
                raise ValueError(f"criterion profile is unavailable: {profile_ref}")
            profiles.append(profile)
        return tuple(profiles)

    @staticmethod
    def _construct(text: str) -> str:
        lines = [line.strip("# ") for line in text.splitlines() if line.strip("# ")]
        return lines[0][:2_000] if lines else ""

    @staticmethod
    def _acceptance_rule(text: str) -> dict[str, object] | None:
        for pattern in TARGET_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            raw_operator = match.group("operator").lower()
            operator_value = {
                "below": "<",
                "less than": "<",
                "under": "<",
                "at most": "<=",
                "이하": "<=",
                "미만": "<",
                "at least": ">=",
                "no less than": ">=",
                "이상": ">=",
                "초과": ">",
            }.get(raw_operator, raw_operator)
            return {
                "operator": operator_value,
                "target": match.group("target"),
                "unit": match.groupdict().get("unit") or "UNSPECIFIED",
                "decision_rule": "SOURCE_EXTRACTED_CANDIDATE",
            }
        return None

    @staticmethod
    def _computation(text: str, rule: dict[str, object] | None) -> dict[str, object] | None:
        lower = text.lower()
        if any(token in lower for token in ("calculated as", "divided by", "산식")):
            return {
                "type": "FORMULA",
                "expression": None,
                "source_description": text[:1_000],
                "evaluator_binding_digest": None,
            }
        if rule is not None:
            return {
                "type": "TEST_PROCEDURE",
                "procedure": "SOURCE_DEFINED_PROCEDURE_REQUIRED",
            }
        return None

    @staticmethod
    def _authority(spans: tuple[EvidenceSpan, ...]) -> AuthorityState:
        if all(
            span.authority_state in {AuthorityState.OFFICIAL, AuthorityState.APPROVED}
            for span in spans
        ):
            return AuthorityState.OFFICIAL
        return AuthorityState.UNCLASSIFIED

    @staticmethod
    def _digest(value: dict[str, object]) -> str:
        return domain_digest("CRITERION_CONTRACT", "1.0.0", canonical_payload(value))


def evaluate_formula(expression: str, variables: dict[str, Decimal]) -> Decimal:
    return evaluate_reference_formula(expression, variables)
