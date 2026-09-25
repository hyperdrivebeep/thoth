from __future__ import annotations

from typing import Literal, cast

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.closure_open_items import open_item_issues
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, head_set_digest
from thoth.domain.closure import Closure, ClosureOpenItem, ClosureOpenItemV2
from thoth.ports.action import ActionStorePort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ClosureReadInput(ProjectInput):
    closure_id: str = Field(min_length=1, max_length=160)


class ReadinessAssessInput(ProjectInput):
    contract_version: Literal[1, 2] = 1
    closure_scope: str = Field(
        pattern=r"^(THREAD_CYCLE|DECISION_OBJECT|PROJECT_PHASE|PROJECT_ARCHIVE)$"
    )
    scope_ref: str = Field(min_length=1, max_length=160)
    disposition: str = Field(
        pattern=r"^(ACHIEVED|NOT_ACHIEVED|ABSTAINED|DEFERRED|CANCELLED|SUPERSEDED|ARCHIVED_WITH_OPEN_ITEMS)$"
    )
    open_items: tuple[ClosureOpenItem, ...] = ()
    required_receipt_refs: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def validate_v2_items(cls, value: object) -> object:
        if isinstance(value, dict):
            fields = cast(dict[str, object], value)
            if fields.get("contract_version") == 2:
                TypeAdapter(tuple[ClosureOpenItemV2, ...]).validate_python(
                    fields.get("open_items", ())
                )
            return fields
        return value


class DecideInput(ProjectInput):
    readiness_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(DECIDE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2_000)


class FollowupInput(ProjectInput):
    closure_id: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=2_000)
    owner_ref: str = Field(min_length=1, max_length=160)
    trigger_or_due: str = Field(min_length=1, max_length=500)


class ReopenInput(ProjectInput):
    closure_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2_000)
    child_scope_ref: str = Field(min_length=1, max_length=160)


class RetentionPlanInput(ProjectInput):
    closure_id: str = Field(min_length=1, max_length=160)
    retention_policy_ref: str = Field(min_length=1, max_length=160)
    legal_or_security_hold: bool = False
    transfer_target: str | None = Field(default=None, max_length=260)


class PurgePrepareInput(ProjectInput):
    closure_id: str = Field(min_length=1, max_length=160)
    exact_scope_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)


class ClosureHandlers:
    def __init__(
        self,
        *,
        records: ControlRecordStorePort,
        controls: ControlRecordService,
        ledger: LedgerPort,
        actions: ActionStorePort,
        executions: ExecutionStorePort,
        outcomes: OutcomeStorePort,
        baselines: BaselineService,
        unit_of_work: AtomicUnitOfWorkPort,
    ) -> None:
        self._records = records
        self._controls = controls
        self._ledger = ledger
        self._actions = actions
        self._executions = executions
        self._outcomes = outcomes
        self._baselines = baselines
        self._unit_of_work = unit_of_work

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectInput.model_validate(value)
        return {
            "closures": [
                item.model_dump(mode="json")
                for item in self._records.list(request.project_id, "CLOSURE", "DECISION")
            ]
        }

    async def readiness_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        item = self._record(request, "READINESS")
        return {"readiness": item.model_dump(mode="json")}

    async def package_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        closure, head = self._ledger_closure(request.project_id, request.closure_id)
        return {
            "closure_package": closure.model_dump(mode="json"),
            "head_digest": head,
            "prepared_not_decided": closure.status.value != "CLOSED",
        }

    async def open_item_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "CLOSURE", "OPEN_ITEM")
            if item.payload.get("closure_id") == request.closure_id
        )
        return {"open_items": [item.model_dump(mode="json") for item in items]}

    async def reopen_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "CLOSURE", "REOPEN")
            if item.payload.get("closure_id") == request.closure_id
        )
        return {"reopens": [item.model_dump(mode="json") for item in items]}

    async def retention_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "CLOSURE", "RETENTION")
            if item.payload.get("closure_id") == request.closure_id
        )
        return {"retention": [item.model_dump(mode="json") for item in items]}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ClosureReadInput.model_validate(value)
        items = tuple(
            item
            for item in self._records.list(request.project_id, "CLOSURE", None, latest_only=False)
            if item.record_id == request.closure_id
            or item.payload.get("closure_id") == request.closure_id
        )
        return {"records": [item.model_dump(mode="json") for item in items]}

    def _assess_readiness(self, request: ReadinessAssessInput) -> tuple[dict[str, object], bool]:
        active_effects = tuple(
            item.plan_execution_id
            for item in self._executions.list_executions(request.project_id)
            if item.state
            in {
                "RUNNING",
                "CANCEL_REQUESTED",
                "COMPENSATION_PENDING",
                "COMPENSATING",
                "UNKNOWN_COMPLETION",
                "EFFECT_MAY_CONTINUE",
            }
        )
        protected_actions = tuple(
            item.action_id
            for item in self._actions.list_actions(request.project_id)
            if item.authorization_state in {"NOT_PREPARED", "PENDING"} and item.risk_tier == "R3"
        )
        receipts = {
            item.receipt_id: item for item in self._ledger.read_receipts(request.project_id)
        }
        missing_receipts = tuple(
            item for item in request.required_receipt_refs if item not in receipts
        )
        conflicts = tuple(
            item.record_id
            for item in self._records.list(request.project_id, "REVISION", "CONFLICT")
            if item.state == "OPEN_CONFLICT"
        )
        baseline_sets = self._baselines.list_sets(request.project_id)
        stale_baselines = tuple(
            item.baseline_set_id for item in baseline_sets if item.lifecycle == "STALE"
        )
        item_issues = open_item_issues(
            request.project_id, request.open_items, self._ledger, self._records
        )
        retention_holds = tuple(
            r.record_id
            for r in self._records.list(request.project_id, "CLOSURE", "RETENTION")
            if r.state == "ON_HOLD"
        )
        blocked = bool(
            active_effects
            or protected_actions
            or missing_receipts
            or conflicts
            or stale_baselines
            or item_issues
            or retention_holds
        )
        readiness = (
            "NOT_READY" if blocked else "READY_WITH_OPEN_ITEMS" if request.open_items else "READY"
        )
        payload: dict[str, object] = {
            "contract_version": request.contract_version,
            "closure_scope": request.closure_scope,
            "scope_ref": request.scope_ref,
            "disposition": request.disposition,
            "scoped_head_set": dict(self._ledger.read_heads(request.project_id)),
            "head_set_digest": head_set_digest(self._ledger.read_heads(request.project_id)),
            "terminal_outcomes": tuple(
                item.outcome_assessment_id
                for item in self._outcomes.list_assessments(request.project_id)
                if item.object_id == request.scope_ref
            ),
            "active_effects": active_effects,
            "protected_actions": protected_actions,
            "missing_receipts": missing_receipts,
            "open_conflicts": conflicts,
            "current_baseline_set_digests": tuple(
                item.baseline_set_digest for item in baseline_sets if item.lifecycle == "CURRENT"
            ),
            "stale_baseline_set_ids": stale_baselines,
            "required_receipt_refs": request.required_receipt_refs,
            "open_items": tuple(
                item.model_dump(mode="json", exclude_unset=True) for item in request.open_items
            ),
            "open_item_issues": item_issues,
            "retention_holds": retention_holds,
            "readiness": readiness,
        }
        return payload, blocked

    async def readiness_assess(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = ReadinessAssessInput.model_validate(value)
            payload, blocked = self._assess_readiness(request)
            readiness = str(payload["readiness"])
            record = self._controls.create(
                project_id=request.project_id,
                namespace="CLOSURE",
                record_type="READINESS",
                state=readiness,
                payload=payload,
            )
            for item in request.open_items:
                self._controls.create(
                    project_id=request.project_id,
                    namespace="CLOSURE",
                    record_type="OPEN_ITEM",
                    state="OPEN",
                    payload={
                        **item.model_dump(mode="json", exclude_unset=True),
                        "closure_id": record.record_id,
                    },
                )
            return {
                "readiness": record.model_dump(mode="json"),
                "blocked": blocked,
            }

    async def decide(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            request = DecideInput.model_validate(value)
            readiness = self._records.read(request.project_id, "CLOSURE", request.readiness_id)
            if readiness is None or readiness.record_type != "READINESS":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "readiness not found")
            if request.decision == "DECIDE" and readiness.state == "NOT_READY":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "closure is blocked")
            current_payload, _ = self._assess_readiness(
                ReadinessAssessInput.model_validate(
                    {
                        "project_id": request.project_id,
                        "contract_version": readiness.payload.get("contract_version", 1),
                        "closure_scope": readiness.payload.get("closure_scope"),
                        "scope_ref": readiness.payload.get("scope_ref"),
                        "disposition": readiness.payload.get("disposition"),
                        "open_items": readiness.payload.get("open_items", ()),
                        "required_receipt_refs": readiness.payload.get("required_receipt_refs", ()),
                    }
                )
            )
            if current_payload.get("open_item_issues"):
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "OPEN_ITEM_DETAILS_REQUIRED"
                )
            if canonical_payload(current_payload) != canonical_payload(readiness.payload):
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "CLOSURE_READINESS_STALE")
            decision = self._controls.create(
                project_id=request.project_id,
                namespace="CLOSURE",
                record_type="DECISION",
                state="DECIDED" if request.decision == "DECIDE" else "REJECTED",
                payload={
                    "readiness_id": readiness.record_id,
                    "closure_scope": readiness.payload.get("closure_scope"),
                    "scope_ref": readiness.payload.get("scope_ref"),
                    "disposition": readiness.payload.get("disposition"),
                    "actor_ref": request.actor_ref,
                    "reason": request.reason,
                    "execution_completion_implies_success": False,
                    "retention_state": "ACTIVE",
                    "open_items": current_payload["open_items"],
                },
            )
            return {"closure": decision.model_dump(mode="json")}

    async def followup_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            return self._create_followup(value)

    def _create_followup(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = FollowupInput.model_validate(value)
        parent = self._records.read(request.project_id, "CLOSURE", request.closure_id)
        if parent is None or parent.record_type != "DECISION" or parent.state != "DECIDED":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "CLOSURE_DECISION_REQUIRED")
        followup = self._controls.create(
            project_id=request.project_id,
            namespace="CLOSURE",
            record_type="FOLLOWUP",
            state="OPEN",
            payload={
                "closure_id": request.closure_id,
                "purpose": request.purpose,
                "owner_ref": request.owner_ref,
                "trigger_or_due": request.trigger_or_due,
                "automatic_real_world_action": False,
            },
        )
        return {"followup": followup.model_dump(mode="json")}

    async def reopen(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        with self._unit_of_work.transaction():
            return self._reopen(value)

    def _reopen(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReopenInput.model_validate(value)
        parent = self._records.read(request.project_id, "CLOSURE", request.closure_id)
        if parent is None or parent.record_type != "DECISION" or parent.state != "DECIDED":
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "CLOSURE_DECISION_REQUIRED")
        record = self._controls.create(
            project_id=request.project_id,
            namespace="CLOSURE",
            record_type="REOPEN",
            state="CHILD_CREATED",
            payload={
                "closure_id": request.closure_id,
                "reason": request.reason,
                "child_scope_ref": request.child_scope_ref,
                "prior_closure_deleted": False,
                "open_items": parent.payload.get("open_items", ()),
            },
        )
        return {"reopen": record.model_dump(mode="json")}

    async def retention_plan(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RetentionPlanInput.model_validate(value)
        state = (
            "ON_HOLD"
            if request.legal_or_security_hold
            else "TRANSFER_READY"
            if request.transfer_target
            else "SCHEDULED"
        )
        record = self._controls.create(
            project_id=request.project_id,
            namespace="CLOSURE",
            record_type="RETENTION",
            state=state,
            payload={
                "closure_id": request.closure_id,
                "retention_policy_ref": request.retention_policy_ref,
                "legal_or_security_hold": request.legal_or_security_hold,
                "transfer_target": request.transfer_target,
                "archive_implies_delete": False,
            },
        )
        return {"retention": record.model_dump(mode="json")}

    async def purge_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PurgePrepareInput.model_validate(value)
        record = self._controls.create(
            project_id=request.project_id,
            namespace="CLOSURE",
            record_type="PURGE_ACTION_CANDIDATE",
            state="HUMAN_REQUIRED_R3",
            payload={
                "closure_id": request.closure_id,
                "exact_scope_refs": request.exact_scope_refs,
                "reason": request.reason,
                "risk_tier": "R3",
                "deletion_performed": False,
                "external_transfer_performed": False,
            },
        )
        return {
            "purge_action_candidate": record.model_dump(mode="json"),
            "deletion_performed": False,
        }

    def _record(self, request: ClosureReadInput, record_type: str):
        item = self._records.read(request.project_id, "CLOSURE", request.closure_id)
        if item is None or item.record_type != record_type:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, f"{record_type} not found")
        return item

    def _ledger_closure(self, project_id: str, closure_id: str) -> tuple[Closure, str]:
        head = self._ledger.read_heads(project_id).get(f"CLOSURE:{closure_id}")
        if head is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "package not found")
        revision = self._ledger.read_revision_by_digest(project_id, head)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "package missing")
        return Closure.model_validate(snapshot.content), head
