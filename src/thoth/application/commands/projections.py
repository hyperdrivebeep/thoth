from __future__ import annotations

from datetime import timedelta

from pydantic import Field, JsonValue

from thoth.application.services import build_recall_context, prepare_protected_action
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.domain.action import ActionPlan
from thoth.domain.base import DomainModel
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.enums import ExecutionAuthority
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.receipt import calculate_receipt_digest
from thoth.domain.research_identity import ResearchIdentityError
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import MemoryStorePort
from thoth.ports.runtime import ClockPort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ObjectProjectionInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    object_id: str = Field(min_length=1, max_length=160)


class MemoryContextInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    character_budget: int = Field(default=20_000, ge=0, le=2_000_000)


class ReceiptInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    receipt_id: str = Field(min_length=1, max_length=160)


class OutcomeInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    outcome_id: str = Field(min_length=1, max_length=160)


class CriterionReadInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    criterion_id: str = Field(min_length=1, max_length=160)


class ProjectionQueryHandlers:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        memory: MemoryStorePort | None = None,
        artifacts: ArtifactLedgerPort,
        threads: ThreadStorePort | None = None,
        clock: ClockPort,
        research: ResearchIdentityService | None = None,
    ) -> None:
        self._ledger = ledger
        self._memory = memory
        self._artifacts = artifacts
        self._threads = threads
        self._clock = clock
        self._research = research

    async def hypothesis(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectProjectionInput.model_validate(value)
        projection = self._current_projection(
            request.project_id,
            request.object_id,
            prefix="HYPOTHESIS:",
            model=HypothesisPortfolio,
        )
        return {"portfolio": projection}

    async def action(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectProjectionInput.model_validate(value)
        projection = self._current_projection(
            request.project_id,
            request.object_id,
            prefix="ACTION:",
            model=ActionPlan,
        )
        return {"action_plan": projection}

    async def criteria_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MemoryContextInput.model_validate(value)
        criteria: list[JsonValue] = []
        for key, digest in sorted(self._ledger.read_heads(request.project_id).items()):
            if not key.startswith("CRITERION:"):
                continue
            revision = self._ledger.read_revision_by_digest(request.project_id, digest)
            snapshot = (
                None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
            )
            if revision is None or snapshot is None:
                continue
            criterion = CriterionCandidate.model_validate(snapshot.content)
            criteria.append(
                {
                    "head_digest": digest,
                    "revision_id": revision.revision_id,
                    "value": criterion.model_dump(mode="json"),
                }
            )
        return {"criteria": criteria}

    async def criteria_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CriterionReadInput.model_validate(value)
        head = self._ledger.read_heads(request.project_id).get(f"CRITERION:{request.criterion_id}")
        if head is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "criterion was not found")
        revision = self._ledger.read_revision_by_digest(request.project_id, head)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise RpcApplicationError(RpcErrorCode.INTERNAL_ERROR, "criterion snapshot is missing")
        criterion = CriterionCandidate.model_validate(snapshot.content)
        return {
            "head_digest": head,
            "revision_id": revision.revision_id,
            "criterion": criterion.model_dump(mode="json"),
        }

    async def object_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self._threads is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "THREAD_PROJECTION_NOT_CONFIGURED"
            )
        request = MemoryContextInput.model_validate(value)
        objects: list[JsonValue] = [
            {
                "object_id": object_id,
                "project_id": thread.project_id,
                "thread_id": thread.thread_id,
                "problem": thread.problem,
                "thread_lifecycle": thread.lifecycle.value,
                "materialization_state": "THREAD_BOUND_PROJECTION",
            }
            for thread in self._threads.list(request.project_id)
            for object_id in thread.current_object_ids
        ]
        return {"objects": objects}

    async def object_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectProjectionInput.model_validate(value)
        objects_result = await self.object_list(
            {"project_id": request.project_id, "character_budget": 0}
        )
        objects = objects_result["objects"]
        if isinstance(objects, list):
            for candidate in objects:
                if isinstance(candidate, dict) and candidate.get("object_id") == request.object_id:
                    return {"object": candidate}
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "object was not found")

    async def action_preflight(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObjectProjectionInput.model_validate(value)
        projection = self._current_projection(
            request.project_id,
            request.object_id,
            prefix="ACTION:",
            model=ActionPlan,
        )
        plan_value = projection["value"]
        plan = ActionPlan.model_validate(plan_value)
        head = projection["head_digest"]
        assert isinstance(head, str)
        from thoth.application.services.research_freshness import ResearchFreshnessService

        currentness = ResearchFreshnessService(self._ledger).evaluate_entity(
            request.project_id, f"ACTION:{plan.plan_id}", head
        )
        if currentness.state != "CURRENT":
            return {
                "plan_id": plan.plan_id,
                "head_digest": head,
                "cards": [],
                "currentness": currentness.model_dump(mode="json"),
            }
        cards: list[JsonValue] = []
        for action in plan.alternatives:
            if action.execution_authority != ExecutionAuthority.HUMAN_REQUIRED_R3:
                continue
            digests = tuple(
                span.text_sha256
                for reference in action.source_refs
                if (span := self._artifacts.read_evidence(reference)) is not None
            )
            cards.append(
                prepare_protected_action(
                    action,
                    plan_revision_digest=head,
                    step_id=f"preflight:{action.action_id}",
                    exact_input_digests=digests or (head,),
                    target_revision=head,
                    baseline_revision=None,
                    tool=action.action_family,
                    environment="user-approved-environment-required",
                    egress="deny-until-approved",
                    budget=str(action.estimated_cost or "not-specified"),
                    time_limit_seconds=action.estimated_seconds or 3600,
                    stop_conditions=("approval expires", "input digest changes"),
                    compensation="defined by approver before execution",
                    required_roles=(action.required_approver_role or "project-owner",),
                    expires_at=self._clock.now() + timedelta(hours=1),
                ).model_dump(mode="json")
            )
        return {"plan_id": plan.plan_id, "head_digest": head, "cards": cards}

    async def memory_context(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self._memory is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "MEMORY_PROJECTION_NOT_CONFIGURED"
            )
        request = MemoryContextInput.model_validate(value)
        heads = self._ledger.read_heads(request.project_id)
        from thoth.application.services.research_freshness import ResearchFreshnessService

        context = build_recall_context(
            project_id=request.project_id,
            records=self._memory.list(request.project_id),
            current_revision_refs=frozenset(heads.values()),
            character_budget=request.character_budget,
            ineligible_owner_refs=ResearchFreshnessService(self._ledger).ineligible_owner_refs(
                request.project_id
            ),
        )
        return {"memory_context": context.model_dump(mode="json")}

    async def outcome(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OutcomeInput.model_validate(value)
        head = self._ledger.read_heads(request.project_id).get(f"OUTCOME:{request.outcome_id}")
        if head is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "outcome was not found")
        revision = self._ledger.read_revision_by_digest(request.project_id, head)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise RpcApplicationError(RpcErrorCode.INTERNAL_ERROR, "outcome snapshot is missing")
        outcome = OutcomeRecord.model_validate(snapshot.content)
        return {
            "head_digest": head,
            "revision_id": revision.revision_id,
            "outcome": outcome.model_dump(mode="json"),
        }

    async def receipt(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReceiptInput.model_validate(value)
        receipt = next(
            (
                item
                for item in self._ledger.read_receipts(request.project_id)
                if item.receipt_id == request.receipt_id
            ),
            None,
        )
        if receipt is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "receipt was not found")
        return {"receipt": receipt.model_dump(mode="json")}

    async def verify_receipt(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReceiptInput.model_validate(value)
        receipt = next(
            (
                item
                for item in self._ledger.read_receipts(request.project_id)
                if item.receipt_id == request.receipt_id
            ),
            None,
        )
        if receipt is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "receipt was not found")
        calculated = calculate_receipt_digest(receipt)
        return {
            "receipt_id": receipt.receipt_id,
            "valid": calculated == receipt.receipt_digest,
            "stored_digest": receipt.receipt_digest,
            "calculated_digest": calculated,
            "semantic_truth_certified": receipt.semantic_truth_certified,
        }

    def _current_projection(
        self,
        project_id: str,
        object_id: str,
        *,
        prefix: str,
        model: type[HypothesisPortfolio] | type[ActionPlan],
    ) -> dict[str, JsonValue]:
        if self._research is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "RESEARCH_IDENTITY_NOT_CONFIGURED"
            )
        heads = dict(self._ledger.read_heads(project_id))
        try:
            context = self._research.context(project_id, object_id, heads)
        except ResearchIdentityError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, exc.reason_code) from exc
        view = context.portfolio_view if model is HypothesisPortfolio else context.action_plan_view
        if view is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "RESEARCH_VIEW_UNAVAILABLE")
        identifier = view.portfolio_id if isinstance(view, HypothesisPortfolio) else view.plan_id
        digest = heads[prefix + identifier]
        revision = self._ledger.read_revision_by_digest(project_id, digest)
        if revision is None:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "RESEARCH_IDENTITY_SOURCE_MISSING"
            )
        return {
            "head_digest": digest,
            "revision_id": revision.revision_id,
            "value": view.model_dump(mode="json"),
            "unrepresented_refs": list(context.unrepresented_refs),
        }
