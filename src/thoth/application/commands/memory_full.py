from __future__ import annotations

from functools import partial
from typing import cast

from pydantic import JsonValue

from thoth.application.commands.memory_control_drafts import (
    candidate_payload,
    classified_payload,
    context_pack_payload,
    gate_draft,
    gate_plan,
    memory_entry_payload,
    projection_checkpoint,
    retention_candidates,
)
from thoth.application.commands.memory_control_profiles import POLICIES
from thoth.application.commands.memory_inputs import (
    CandidateClassifyInput,
    CandidateCreateInput,
    CandidateListInput,
    CandidateReadInput,
    ChangeSetProposeInput,
    ConflictListInput,
    ContextBuildInput,
    ContextReadInput,
    GateReadInput,
    MemoryListInput,
    MemoryReadInput,
    PolicyListInput,
    PolicyReadInput,
    ProjectInput,
    ProjectionRebuildInput,
    RecallAuditInput,
    RetentionEvaluateInput,
    RetireProposeInput,
    RevalidateInput,
    ValidateInput,
)
from thoth.application.commands.memory_read_projection import (
    find_policy,
    projection_status_view,
    select_policies,
)
from thoth.application.commands.memory_records import memory_record
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.memory_router import build_recall_context
from thoth.domain.control_record import ControlRecord
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import FullMemoryStorePort, MemoryStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


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
        self._memory_record = partial(memory_record, controls)
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
        policies = select_policies(POLICIES, request)
        return {"policies": [cast(JsonValue, policy) for policy in policies]}

    async def policy_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PolicyReadInput.model_validate(value)
        policy = find_policy(POLICIES, request.profile_ref)
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
        projections = self._full.list_projections(request.project_id)
        return projection_status_view(tasks, projections)

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
        payload, unsafe, missing = candidate_payload(request)
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
                "gate_plan": gate_plan(payload),
                "allowed_use": payload["allowed_use"],
            },
        )

    async def candidate_classify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CandidateClassifyInput.model_validate(value)
        current = self._candidate(request)
        if current.version != request.expected_candidate_revision:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "candidate revision changed")
        recall_class, alternatives, status, payload = classified_payload(current, request)
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
        with self._ledger.transaction():
            current = self._candidate(request)
            plan, reducer, verdicts, gate_payload, gate_digest = gate_draft(request, current)
            gate = self._memory_record(
                request, "GATE", reducer, {**gate_payload, "gate_digest": gate_digest}
            )
            revised = self._memory_record(
                request,
                "CANDIDATE",
                reducer,
                {**current.payload, "validated_gate_digest": gate_digest},
                record_id=current.record_id,
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
        with self._ledger.transaction():
            candidate = self._candidate(request)
            if (
                candidate.state != "VALIDATED"
                or candidate.payload.get("validated_gate_digest") != request.validated_gate_digest
            ):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "candidate gate is not validated"
                )
            entry_payload = memory_entry_payload(candidate)
            memory_candidate = self._memory_record(
                request, "MEMORY_ENTRY_CANDIDATE", "PROPOSED_NOT_APPLIED", entry_payload
            )
            change_set = self._memory_record(
                request,
                "CHANGE_SET_PROPOSAL",
                "PROPOSED_NOT_APPLIED",
                {
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
        with self._ledger.transaction():
            current = self._memory(request)
            if current.record_digest != request.expected_revision_digest:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "memory revision changed")
            candidate = self._memory_record(
                request,
                "RETIRE_CANDIDATE",
                "PROPOSED_NOT_APPLIED",
                {
                    "memory_entry_id": current.record_id,
                    "reason": request.reason,
                    "evidence_refs": request.evidence_refs,
                    "history_preserved": True,
                },
            )
            change_set = self._memory_record(
                request,
                "CHANGE_SET_PROPOSAL",
                "PROPOSED_NOT_APPLIED",
                {"retire_candidate_digest": candidate.record_digest},
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
        included, pack_payload = context_pack_payload(request, entries, heads)
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
        checkpoint = projection_checkpoint(request, source_set)
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
        candidates = retention_candidates(entries)
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
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, tuple | list):
            return ()
        return tuple(str(item) for item in cast(tuple[object, ...] | list[object], value))
