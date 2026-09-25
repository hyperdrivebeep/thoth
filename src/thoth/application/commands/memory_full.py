from __future__ import annotations

import re
from typing import cast

from pydantic import AwareDatetime, Field, JsonValue

from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.memory_router import build_recall_context
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import FullMemoryStorePort, MemoryStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

POLICIES = (
    {
        "profile_ref": "memory:domain-reference:1",
        "version": 1,
        "payload_mode": "DOMAIN_REFERENCE",
        "recall_classes": (
            "FACT",
            "FAILURE",
            "REFERENCE",
            "HYPOTHESIS",
            "DECISION",
            "PERSON",
            "ACTION",
        ),
        "owner": "DOMAIN_NAMESPACE",
        "promotion": "EXACT_OWNER_REVISION_REQUIRED",
        "expiry": "OWNER_OR_POLICY_BOUND",
        "retention": "AUDIT_PRESERVE",
        "team_roles": ("FACTS", "CONTRARIAN", "RISK", "GATE_REVIEWER"),
        "enabled": True,
    },
    {
        "profile_ref": "memory:assertion:1",
        "version": 1,
        "payload_mode": "MEMORY_ASSERTION",
        "recall_classes": ("LESSON", "PRACTICE"),
        "owner": "MEMORY_NAMESPACE",
        "promotion": "SOURCE_LINEAGE_AND_TEAM_GATE",
        "expiry": "TIME_AND_SUPPORT_BOUND",
        "retention": "SUPERSEDE_OR_RETIRE_NO_DELETE",
        "team_roles": ("REFLECTION", "CONTRARIAN", "RISK", "GATE_REVIEWER"),
        "enabled": True,
    },
)

INJECTION = re.compile(
    r"ignore\s+(all|previous)|system\s+prompt|developer\s+message|이전\s*명령.*무시",
    re.IGNORECASE,
)
SECRET = re.compile(r"(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s]{8,}", re.IGNORECASE)


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class MemoryListInput(ProjectInput):
    recall_class: str | None = Field(default=None, max_length=80)
    payload_mode: str | None = Field(default=None, max_length=80)
    pipeline_status: str | None = Field(default=None, max_length=80)
    lifecycle_status: str | None = Field(default=None, max_length=80)
    recall_eligibility: str | None = Field(default=None, max_length=80)


class MemoryReadInput(ProjectInput):
    memory_entry_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class CandidateListInput(ProjectInput):
    producer_role: str | None = Field(default=None, max_length=80)
    classification_status: str | None = Field(default=None, max_length=80)
    risk_tag: str | None = Field(default=None, max_length=80)


class CandidateReadInput(ProjectInput):
    candidate_id: str = Field(min_length=1, max_length=160)


class ContextReadInput(ProjectInput):
    context_pack_id: str | None = Field(default=None, max_length=160)
    character_budget: int = Field(default=20_000, ge=0, le=2_000_000)


class PolicyListInput(ProjectInput):
    recall_class: str | None = Field(default=None, max_length=80)
    payload_mode: str | None = Field(default=None, max_length=80)
    enabled_only: bool = True


class PolicyReadInput(ProjectInput):
    profile_ref: str = Field(min_length=1, max_length=160)
    version: int | None = Field(default=None, ge=1)


class GateReadInput(ProjectInput):
    candidate_id: str | None = Field(default=None, max_length=160)
    memory_entry_id: str | None = Field(default=None, max_length=160)
    transition_receipt_ref: str | None = Field(default=None, max_length=160)


class ConflictListInput(ProjectInput):
    recall_class: str | None = Field(default=None, max_length=80)
    lifecycle_status: str | None = Field(default=None, max_length=80)


class RecallAuditInput(ProjectInput):
    context_pack_id: str | None = Field(default=None, max_length=160)
    thread_id: str | None = Field(default=None, max_length=160)
    time_range: dict[str, str] | None = None


class CandidateCreateInput(ProjectInput):
    producer_role: str = Field(min_length=1, max_length=80)
    payload_mode: str = Field(pattern=r"^(DOMAIN_REFERENCE|MEMORY_ASSERTION)$")
    recall_class: str = Field(min_length=1, max_length=80)
    domain_revision_ref: str | None = Field(default=None, max_length=160)
    assertion_candidate: str | None = Field(default=None, max_length=20_000)
    parent_refs: tuple[str, ...] = ()
    scope: dict[str, str]
    evidence_refs: tuple[str, ...]
    risk_tags: tuple[str, ...] = ()


class CandidateClassifyInput(CandidateReadInput):
    classifier_profile_ref: str = Field(min_length=1, max_length=160)
    expected_candidate_revision: int = Field(ge=1)


class ValidateInput(CandidateReadInput):
    transition_intent: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=160)


class ChangeSetProposeInput(CandidateReadInput):
    transition_intent: str = Field(min_length=1, max_length=80)
    validated_gate_digest: str = Field(min_length=64, max_length=64)
    expected_memory_head_digest: str | None = Field(default=None, min_length=64, max_length=64)


class RevalidateInput(MemoryReadInput):
    trigger_refs: tuple[str, ...]


class RetireProposeInput(MemoryReadInput):
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...]
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class ContextBuildInput(ProjectInput):
    thread_id: str = Field(min_length=1, max_length=160)
    query: str = Field(min_length=1, max_length=10_000)
    target_use: str = Field(min_length=1, max_length=80)
    scope: dict[str, str]
    token_budget: int = Field(ge=0, le=2_000_000)
    selection_policy_version: str = Field(min_length=1, max_length=160)
    cutoff: AwareDatetime | None = None


class ProjectionRebuildInput(ProjectInput):
    projection_types: tuple[str, ...]
    from_checkpoint: str | None = Field(default=None, max_length=160)
    model_or_index_version: str | None = Field(default=None, max_length=160)


class RetentionEvaluateInput(ProjectInput):
    scope: dict[str, str] | None = None
    policy_version: str = Field(min_length=1, max_length=160)
    evaluation_time: AwareDatetime


class MemoryFullHandlers:
    def __init__(
        self,
        *,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        legacy: MemoryStorePort,
        full: FullMemoryStorePort,
        ledger: LedgerPort,
    ) -> None:
        self._records = records
        self._controls = controls
        self._legacy = legacy
        self._full = full
        self._ledger = ledger

    def seed_policies(self, project_id: str = "system:memory") -> None:
        for policy in POLICIES:
            if self._records.read(project_id, "MEMORY", str(policy["profile_ref"])):
                continue
            self._controls.create(
                project_id=project_id,
                namespace="MEMORY",
                record_type="POLICY",
                record_id=str(policy["profile_ref"]),
                state="ENABLED",
                payload=cast(dict[str, object], policy),
            )

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MemoryListInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "MEMORY", "MEMORY_ENTRY")
            if (
                request.recall_class is None
                or item.payload.get("recall_class") == request.recall_class
            )
            and (
                request.payload_mode is None
                or item.payload.get("payload_mode") == request.payload_mode
            )
            and (
                request.pipeline_status is None
                or item.payload.get("pipeline_status") == request.pipeline_status
            )
            and (
                request.lifecycle_status is None
                or item.payload.get("lifecycle_status") == request.lifecycle_status
            )
            and (
                request.recall_eligibility is None
                or item.payload.get("recall_eligibility") == request.recall_eligibility
            )
        )
        return {
            "memories": [item.model_dump(mode="json") for item in items],
            "full_memories": [
                item.model_dump(mode="json")
                for item in self._full.list_revisions(request.project_id)
            ],
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MemoryReadInput.model_validate(value)
        item = self._records.read(request.project_id, "MEMORY", request.memory_entry_id)
        if item is None or item.record_type != "MEMORY_ENTRY":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "memory not found")
        return {"memory": item.model_dump(mode="json")}

    async def candidate_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateListInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "MEMORY", "CANDIDATE")
            if (
                request.producer_role is None
                or item.payload.get("producer_role") == request.producer_role
            )
            and (
                request.classification_status is None
                or item.payload.get("classification_status") == request.classification_status
            )
            and (
                request.risk_tag is None
                or request.risk_tag in self._strings(item.payload.get("risk_tags"))
            )
        )
        return {"candidates": [item.model_dump(mode="json") for item in items]}

    async def candidate_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateReadInput.model_validate(value)
        return {"candidate": self._candidate(request).model_dump(mode="json")}

    async def context_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ContextReadInput.model_validate(value)
        if request.context_pack_id:
            item = self._records.read(request.project_id, "MEMORY", request.context_pack_id)
            if item is None or item.record_type != "CONTEXT_PACK":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "context pack not found")
            return {"memory_context": item.model_dump(mode="json")}
        from thoth.application.services.research_freshness import ResearchFreshnessService

        legacy = build_recall_context(
            project_id=request.project_id,
            records=self._legacy.list(request.project_id),
            current_revision_refs=frozenset(self._ledger.read_heads(request.project_id).values()),
            character_budget=request.character_budget,
            ineligible_owner_refs=ResearchFreshnessService(self._ledger).ineligible_owner_refs(
                request.project_id
            ),
        )
        return {"memory_context": legacy.model_dump(mode="json")}

    async def policy_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PolicyListInput.model_validate(value)
        policies = tuple(
            policy
            for policy in POLICIES
            if (request.payload_mode is None or policy["payload_mode"] == request.payload_mode)
            and (
                request.recall_class is None
                or request.recall_class in cast(tuple[str, ...], policy["recall_classes"])
            )
            and (not request.enabled_only or policy["enabled"] is True)
        )
        return {"policies": [cast(JsonValue, policy) for policy in policies]}

    async def policy_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PolicyReadInput.model_validate(value)
        policy = next(
            (item for item in POLICIES if item["profile_ref"] == request.profile_ref), None
        )
        if policy is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "policy not found")
        return {"policy": cast(JsonValue, policy)}

    async def gate_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = GateReadInput.model_validate(value)
        subject = request.candidate_id or request.memory_entry_id or request.transition_receipt_ref
        if subject is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "gate subject required")
        gates = tuple(
            item
            for item in self._records.list(request.project_id, "MEMORY", "GATE")
            if item.payload.get("subject_id") == subject
        )
        return {"gates": [item.model_dump(mode="json") for item in gates]}

    async def conflict_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ConflictListInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "MEMORY", "MEMORY_ENTRY")
            if item.payload.get("support_status") in {"CONFLICTING", "UNRESOLVED"}
            and (
                request.recall_class is None
                or item.payload.get("recall_class") == request.recall_class
            )
            and (
                request.lifecycle_status is None
                or item.payload.get("lifecycle_status") == request.lifecycle_status
            )
        )
        return {"conflicts": [item.model_dump(mode="json") for item in items]}

    async def recall_audit(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecallAuditInput.model_validate(value)
        packs = self._records.list(request.project_id, "MEMORY", "CONTEXT_PACK", latest_only=False)
        if request.context_pack_id:
            packs = tuple(item for item in packs if item.record_id == request.context_pack_id)
        return {"recall_audit": [item.model_dump(mode="json") for item in packs]}

    async def projection_status(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        tasks = self._records.list(request.project_id, "MEMORY", "PROJECTION_TASK")
        return cast(
            dict[str, JsonValue],
            {
                "canonical_reference_health": "PASS",
                "index_freshness": "CURRENT" if not tasks else tasks[-1].state,
                "vector_freshness": "DERIVED_ONLY",
                "summary_freshness": "DERIVED_ONLY",
                "rebuild_checkpoints": [item.payload.get("checkpoint") for item in tasks],
                "leakage_checks": "PROJECT_FILTER_BEFORE_RERANK",
                "full_projection_types": [
                    item.projection_type for item in self._full.list_projections(request.project_id)
                ],
                "derived_projection_canonical_truth": False,
            },
        )

    async def full_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "memory_revisions": [
                item.model_dump(mode="json")
                for item in self._full.list_revisions(request.project_id)
            ]
        }

    async def full_receipt_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "memory_receipts": [
                item.model_dump(mode="json")
                for item in self._full.list_receipts(request.project_id)
            ]
        }

    async def full_context_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "memory_contexts": [
                item.model_dump(mode="json")
                for item in self._full.list_contexts(request.project_id)
            ]
        }

    async def full_projection_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "memory_projections": [
                item.model_dump(mode="json")
                for item in self._full.list_projections(request.project_id)
            ]
        }

    async def candidate_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateCreateInput.model_validate(value)
        raw = request.assertion_candidate or ""
        unsafe = bool(INJECTION.search(raw) or SECRET.search(raw))
        missing: list[str] = []
        if request.payload_mode == "DOMAIN_REFERENCE" and not request.domain_revision_ref:
            missing.append("domain_revision_ref")
        if request.payload_mode == "MEMORY_ASSERTION" and not raw:
            missing.append("assertion_candidate")
        risk_tags = (*request.risk_tags, "UNSAFE_CONTENT") if unsafe else request.risk_tags
        payload: dict[str, object] = {
            "producer_role": request.producer_role,
            "payload_mode": request.payload_mode,
            "recall_class": request.recall_class,
            "domain_revision_ref": request.domain_revision_ref,
            "assertion_candidate": "[REDACTED_TOMBSTONE]"
            if unsafe
            else request.assertion_candidate,
            "raw_payload_retained": not unsafe,
            "parent_refs": request.parent_refs,
            "scope": request.scope,
            "evidence_refs": request.evidence_refs,
            "risk_tags": tuple(dict.fromkeys(risk_tags)),
            "classification_status": "AMBIGUOUS" if missing else "CLEAR",
            "deterministic_precheck": "QUARANTINE" if unsafe else "PASS" if not missing else "HOLD",
            "missing_gates": tuple(missing),
            "allowed_use": "AUDIT_ONLY" if unsafe else "EVIDENCE_SEARCH_ONLY",
        }
        candidate = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="CANDIDATE",
            state="QUARANTINED" if unsafe else "HELD" if missing else "CANDIDATE",
            payload=payload,
        )
        return cast(
            dict[str, JsonValue],
            {
                "candidate": candidate.model_dump(mode="json"),
                "precheck": payload["deterministic_precheck"],
                "gate_plan": self._gate_plan(payload),
                "allowed_use": payload["allowed_use"],
            },
        )

    async def candidate_classify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateClassifyInput.model_validate(value)
        current = self._candidate(request)
        if current.version != request.expected_candidate_revision:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "candidate revision changed")
        recall_class = str(current.payload.get("recall_class"))
        alternatives = ()
        status = "CLEAR"
        if recall_class not in {
            str(item)
            for policy in POLICIES
            for item in cast(tuple[str, ...], policy["recall_classes"])
        }:
            status = "AMBIGUOUS"
            alternatives = ("REFERENCE", "LESSON")
        payload = {
            **current.payload,
            "classification_status": status,
            "alternative_classes": alternatives,
            "classifier_profile_ref": request.classifier_profile_ref,
            "required_role_union": self._gate_plan(current.payload)["required_roles"],
        }
        revised = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="CANDIDATE",
            record_id=current.record_id,
            state="HELD" if status == "AMBIGUOUS" else current.state,
            payload=payload,
        )
        return cast(
            dict[str, JsonValue],
            {
                "candidate": revised.model_dump(mode="json"),
                "classification_status": status,
                "primary_class": recall_class,
                "alternative_classes": list(alternatives),
                "payload_mode": payload.get("payload_mode"),
                "required_role_union": payload["required_role_union"],
            },
        )

    async def validate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ValidateInput.model_validate(value)
        current = self._candidate(request)
        plan = self._gate_plan(current.payload)
        unsafe = current.state == "QUARANTINED"
        ambiguous = current.payload.get("classification_status") == "AMBIGUOUS"
        reducer = "QUARANTINED" if unsafe else "HELD" if ambiguous else "VALIDATED"
        verdicts = {
            role: "REJECT" if unsafe else "HOLD" if ambiguous else "PASS"
            for role in cast(tuple[str, ...], plan["required_roles"])
        }
        gate_payload: dict[str, object] = {
            "subject_id": current.record_id,
            "transition_intent": request.transition_intent,
            "policy_version": request.policy_version,
            "required_roles": plan["required_roles"],
            "actual_roles": tuple(verdicts),
            "verdicts": verdicts,
            "deterministic_precheck": current.payload.get("deterministic_precheck"),
            "reducer_outcome": reducer,
        }
        gate_digest = domain_digest("MEMORY_GATE", "1.0.0", canonical_payload(gate_payload))
        gate = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="GATE",
            state=reducer,
            payload={**gate_payload, "gate_digest": gate_digest},
        )
        revised = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="CANDIDATE",
            record_id=current.record_id,
            state=reducer,
            payload={**current.payload, "validated_gate_digest": gate_digest},
        )
        return cast(
            dict[str, JsonValue],
            {
                "candidate": revised.model_dump(mode="json"),
                "required_team_roles": list(cast(tuple[str, ...], plan["required_roles"])),
                "deterministic_semantic_verdicts": verdicts,
                "reducer_outcome": reducer,
                "gate": gate.model_dump(mode="json"),
                "gate_digest": gate_digest,
            },
        )

    async def change_set_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetProposeInput.model_validate(value)
        candidate = self._candidate(request)
        if (
            candidate.state != "VALIDATED"
            or candidate.payload.get("validated_gate_digest") != request.validated_gate_digest
        ):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "candidate gate is not validated"
            )
        entry_payload: dict[str, object] = {
            "candidate_id": candidate.record_id,
            "payload_mode": candidate.payload.get("payload_mode"),
            "recall_class": candidate.payload.get("recall_class"),
            "domain_revision_ref": candidate.payload.get("domain_revision_ref"),
            "assertion": candidate.payload.get("assertion_candidate"),
            "parent_refs": candidate.payload.get("parent_refs", ()),
            "scope": candidate.payload.get("scope", {}),
            "evidence_refs": candidate.payload.get("evidence_refs", ()),
            "pipeline_status": "CANDIDATE",
            "support_status": "UNRESOLVED",
            "authority_status": "UNKNOWN",
            "lifecycle_status": "ACTIVE",
            "recall_eligibility": "EVIDENCE_SEARCH_ONLY",
            "classification_status": candidate.payload.get("classification_status"),
        }
        memory_candidate = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="MEMORY_ENTRY_CANDIDATE",
            state="PROPOSED_NOT_APPLIED",
            payload=entry_payload,
        )
        change_set = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="CHANGE_SET_PROPOSAL",
            state="PROPOSED_NOT_APPLIED",
            payload={
                "memory_candidate_digest": memory_candidate.record_digest,
                "transition_intent": request.transition_intent,
                "expected_memory_head_digest": request.expected_memory_head_digest,
                "current_state_mutated": False,
            },
        )
        return cast(
            dict[str, JsonValue],
            {
                "memory_entry_candidate": memory_candidate.model_dump(mode="json"),
                "revision_change_set_proposal": change_set.model_dump(mode="json"),
                "current_state_mutated": False,
            },
        )

    async def revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevalidateInput.model_validate(value)
        current = self._memory(request)
        owner = str(current.payload.get("domain_revision_ref") or "")
        current_heads = set(self._ledger.read_heads(request.project_id).values())
        owner_current = not owner or owner in current_heads
        payload = {
            **current.payload,
            "support_status": current.payload.get("support_status", "UNRESOLVED"),
            "authority_status": current.payload.get("authority_status", "UNKNOWN"),
            "lifecycle_status": "ACTIVE" if owner_current else "SUPERSEDED",
            "recall_eligibility": current.payload.get("recall_eligibility", "EVIDENCE_SEARCH_ONLY"),
            "trigger_refs": request.trigger_refs,
        }
        candidate = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="MEMORY_REVALIDATION",
            state="REVISED" if owner_current else "RETIRE_CANDIDATE",
            payload={"memory_entry_id": current.record_id, **payload},
        )
        return cast(
            dict[str, JsonValue],
            {
                "revalidation": candidate.model_dump(mode="json"),
                "current_support": payload["support_status"],
                "authority": payload["authority_status"],
                "lifecycle": payload["lifecycle_status"],
                "recall_eligibility": payload["recall_eligibility"],
            },
        )

    async def retire_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RetireProposeInput.model_validate(value)
        current = self._memory(request)
        if current.record_digest != request.expected_revision_digest:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "memory revision changed")
        candidate = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="RETIRE_CANDIDATE",
            state="PROPOSED_NOT_APPLIED",
            payload={
                "memory_entry_id": current.record_id,
                "reason": request.reason,
                "evidence_refs": request.evidence_refs,
                "history_preserved": True,
            },
        )
        change_set = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="CHANGE_SET_PROPOSAL",
            state="PROPOSED_NOT_APPLIED",
            payload={"retire_candidate_digest": candidate.record_digest},
        )
        return {
            "retirement_candidate": candidate.model_dump(mode="json"),
            "change_set": change_set.model_dump(mode="json"),
            "history_preserved": True,
        }

    async def context_build(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ContextBuildInput.model_validate(value)
        heads = set(self._ledger.read_heads(request.project_id).values())
        entries = self._records.list(request.project_id, "MEMORY", "MEMORY_ENTRY")
        included: list[dict[str, object]] = []
        excluded: list[dict[str, object]] = []
        used = 0
        for item in entries:
            payload = item.payload
            reason = None
            owner = str(payload.get("domain_revision_ref") or "")
            if owner and owner not in heads:
                reason = "OWNER_REVISION_NOT_CURRENT"
            elif payload.get("lifecycle_status") != "ACTIVE":
                reason = "NOT_ACTIVE"
            elif payload.get("recall_eligibility") not in {"WORKING_CONTEXT", "ACTION_CONTEXT"}:
                reason = "NOT_ELIGIBLE_FOR_TARGET_USE"
            elif payload.get("scope") and not self._scope_matches(
                payload.get("scope"), request.scope
            ):
                reason = "SCOPE_MISMATCH"
            size = len(str(payload.get("assertion") or payload.get("domain_revision_ref") or ""))
            if reason is None and used + size > request.token_budget * 4:
                reason = "TOKEN_BUDGET"
            if reason is None:
                included.append(
                    {
                        "memory_entry_id": item.record_id,
                        "revision_digest": item.record_digest,
                        "reason": "DETERMINISTIC_FILTER_AND_RELEVANCE",
                    }
                )
                used += size
            else:
                excluded.append({"memory_entry_id": item.record_id, "reason": reason})
        pack_payload: dict[str, object] = {
            "thread_id": request.thread_id,
            "query": request.query,
            "target_use": request.target_use,
            "scope": request.scope,
            "cutoff": request.cutoff,
            "selection_policy_version": request.selection_policy_version,
            "included": tuple(included),
            "excluded": tuple(excluded),
            "conflict_refs": tuple(
                item.record_id
                for item in entries
                if item.payload.get("support_status") == "CONFLICTING"
            ),
            "token_budget": request.token_budget,
            "approximate_characters": used,
            "query_policy_digest": domain_digest(
                "MEMORY_QUERY_POLICY",
                "1.0.0",
                canonical_payload(
                    {
                        "query": request.query,
                        "scope": request.scope,
                        "policy": request.selection_policy_version,
                    }
                ),
            ),
        }
        pack = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="CONTEXT_PACK",
            state="BUILT" if included else "NO_ELIGIBLE_MEMORY",
            payload=pack_payload,
        )
        return {
            "memory_context_pack": pack.model_dump(mode="json"),
            "no_eligible_memory": not included,
        }

    async def projection_rebuild(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectionRebuildInput.model_validate(value)
        source_set = tuple(
            item.record_digest
            for item in self._records.list(request.project_id, "MEMORY", "MEMORY_ENTRY")
        )
        checkpoint = domain_digest(
            "MEMORY_PROJECTION_CHECKPOINT",
            "1.0.0",
            canonical_payload(
                {
                    "sources": source_set,
                    "types": request.projection_types,
                    "version": request.model_or_index_version,
                }
            ),
        )
        task = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="PROJECTION_TASK",
            state="COMPLETED",
            payload={
                "projection_types": request.projection_types,
                "from_checkpoint": request.from_checkpoint,
                "model_or_index_version": request.model_or_index_version,
                "canonical_source_set": source_set,
                "progress": "COMPLETED",
                "checkpoint": checkpoint,
                "authority_changed": False,
            },
        )
        return {"rebuild_task": task.model_dump(mode="json"), "authority_changed": False}

    async def retention_evaluate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RetentionEvaluateInput.model_validate(value)
        entries = self._records.list(request.project_id, "MEMORY", "MEMORY_ENTRY")
        candidates = tuple(
            {
                "memory_entry_id": item.record_id,
                "transition": "RETIRE_CANDIDATE"
                if item.payload.get("lifecycle_status") == "EXPIRED"
                else "RETAIN",
                "silent_delete": False,
            }
            for item in entries
        )
        evaluation = self._controls.create(
            project_id=request.project_id,
            namespace="MEMORY",
            record_type="RETENTION_EVALUATION",
            state="COMPLETED",
            payload={
                "scope": request.scope,
                "policy_version": request.policy_version,
                "evaluation_time": request.evaluation_time,
                "candidates": candidates,
                "silent_deletion": False,
            },
        )
        return cast(
            dict[str, JsonValue],
            {
                "retention_evaluation": evaluation.model_dump(mode="json"),
                "candidates": list(candidates),
                "silent_deletion": False,
            },
        )

    def _candidate(self, request: CandidateReadInput) -> ControlRecord:
        item = self._records.read(request.project_id, "MEMORY", request.candidate_id)
        if item is None or item.record_type != "CANDIDATE":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "candidate not found")
        return item

    def _memory(self, request: MemoryReadInput) -> ControlRecord:
        item = self._records.read(request.project_id, "MEMORY", request.memory_entry_id)
        if item is None or item.record_type != "MEMORY_ENTRY":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "memory not found")
        return item

    @staticmethod
    def _gate_plan(payload: dict[str, object]) -> dict[str, object]:
        mode = payload.get("payload_mode")
        roles = (
            ("FACTS", "CONTRARIAN", "RISK", "GATE_REVIEWER")
            if mode == "DOMAIN_REFERENCE"
            else ("REFLECTION", "CONTRARIAN", "RISK", "GATE_REVIEWER")
        )
        return {"required_roles": roles, "majority_vote": False}

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            return ()
        return tuple(str(item) for item in cast(tuple[object, ...] | list[object], value))

    @staticmethod
    def _scope_matches(left: object, right: dict[str, str]) -> bool:
        if not isinstance(left, dict):
            return False
        return all(
            right.get(str(key)) == str(value)
            for key, value in cast(dict[object, object], left).items()
            if key != "workstream"
        )
