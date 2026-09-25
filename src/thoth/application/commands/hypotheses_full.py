from __future__ import annotations

from typing import cast

from pydantic import AwareDatetime, Field, JsonValue

from thoth.application.commands.hypothesis_generation import generate_hypotheses
from thoth.application.services.hypothesis_service import (
    CAUSAL_INTENTS,
    PRIMARY_INTENTS,
    HypothesisService,
)
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class HypothesisListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    primary_intent: str | None = Field(default=None, max_length=80)
    stage: str | None = Field(default=None, max_length=80)
    appraisal: str | None = Field(default=None, max_length=80)
    freshness: str | None = Field(default=None, max_length=40)


class HypothesisReadInput(ProjectInput):
    hypothesis_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class PortfolioListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    stage: str | None = Field(default=None, max_length=80)


class PortfolioReadInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class RelationListInput(ProjectInput):
    hypothesis_id: str | None = Field(default=None, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    relation_type: str | None = Field(default=None, max_length=80)


class PredictionListInput(ProjectInput):
    hypothesis_id: str | None = Field(default=None, max_length=160)
    prespecification_state: str | None = Field(default=None, max_length=80)
    observation_bound: bool | None = None


class PredictionReadInput(ProjectInput):
    prediction_id: str = Field(min_length=1, max_length=160)


class AssumptionListInput(ProjectInput):
    hypothesis_id: str | None = Field(default=None, max_length=160)
    prediction_id: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, max_length=80)


class GenerateInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    question: str = Field(min_length=1, max_length=10_000)
    evidence_scope: tuple[str, ...]
    intent_hints: tuple[str, ...] = ()
    generation_policy_ref: str = Field(min_length=1, max_length=160)
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class CreateInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    portfolio_id: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=10_000)
    primary_intent: str = Field(min_length=1, max_length=80)
    secondary_intents: tuple[str, ...] = ()
    evidence_basis: str = Field(min_length=1, max_length=2_000)
    scope: dict[str, str]
    evidence_refs: tuple[str, ...]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    prespecification_state: str = Field(default="UNKNOWN", max_length=80)


class RevisionBoundInput(HypothesisReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class ReviseInput(RevisionBoundInput):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class IntentUpdateInput(RevisionBoundInput):
    primary_intent: str = Field(min_length=1, max_length=80)
    secondary_intents: tuple[str, ...]
    intent_profile_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class CausalUpdateInput(RevisionBoundInput):
    causal_patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class RelationAddInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    source_hypothesis_id: str = Field(min_length=1, max_length=160)
    relation_type: str = Field(min_length=1, max_length=80)
    target_hypothesis_id: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]
    semantic_role: str | None = Field(default=None, max_length=500)
    expected_portfolio_revision: str = Field(min_length=64, max_length=64)


class RelationRemoveInput(ProjectInput):
    relation_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]
    expected_portfolio_revision: str = Field(min_length=64, max_length=64)


class AssumptionAddInput(RevisionBoundInput):
    statement: str = Field(min_length=1, max_length=5_000)
    role: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]
    validation_route: str | None = Field(default=None, max_length=2_000)


class PredictionBindInput(ProjectInput):
    hypothesis_id: str = Field(min_length=1, max_length=160)
    hypothesis_revision_digest: str = Field(min_length=64, max_length=64)
    knowledge_cutoff: AwareDatetime
    prespecification_state: str = Field(min_length=1, max_length=80)
    conditions: dict[str, str]
    measurement_contract_ref: str = Field(min_length=1, max_length=260)
    assumption_refs: tuple[str, ...]
    expected_outcome: dict[str, JsonValue]
    discrimination_map: dict[str, JsonValue]


class CounterevidenceRequestInput(HypothesisReadInput):
    source_scope: tuple[str, ...]
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class PortfolioComposeInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    hypothesis_ids: tuple[str, ...] = Field(min_length=2)
    relation_candidates: tuple[dict[str, JsonValue], ...] = ()
    unknown_reserve: dict[str, JsonValue]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    portfolio_id: str | None = Field(default=None, max_length=160)


class PortfolioRevalidateInput(PortfolioReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class TestBindInput(ProjectInput):
    prediction_id: str = Field(min_length=1, max_length=160)
    execution_ref: str = Field(min_length=1, max_length=260)
    observation_refs: tuple[str, ...]
    test_validity_assessment_ref: str = Field(min_length=1, max_length=260)
    test_validity: str = Field(
        default="NOT_ASSESSABLE", pattern=r"^(VALID|LIMITED|INVALID|NOT_ASSESSABLE)$"
    )
    prediction_fit: str = Field(
        default="INCONCLUSIVE",
        pattern=r"^(MATCH|PARTIAL_MATCH|MISMATCH|NOT_OBSERVED|INCONCLUSIVE)$",
    )


class AppraiseInput(HypothesisReadInput):
    evidence_refs: tuple[str, ...]
    test_assessment_refs: tuple[str, ...]
    appraisal_scope: dict[str, str]


class SplitProposeInput(RevisionBoundInput):
    subhypotheses: tuple[dict[str, JsonValue], ...]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)


class MergeProposeInput(ProjectInput):
    hypothesis_ids: tuple[str, ...] = Field(min_length=2)
    field_mapping: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)
    expected_revision_digests: tuple[str, ...] = Field(min_length=2)


class HypothesisHandlers:
    def __init__(
        self,
        *,
        store: HypothesisStorePort,
        service: HypothesisService,
        objects: DecisionObjectStorePort,
        artifacts: ArtifactLedgerPort,
        threads: ThreadStorePort,
        investigations: InvestigationService,
    ) -> None:
        self._store = store
        self._service = service
        self._objects = objects
        self._artifacts = artifacts
        self._threads = threads
        self._investigations = investigations

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_hypotheses(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
            and (request.portfolio_id is None or item.portfolio_id == request.portfolio_id)
            and (request.primary_intent is None or item.primary_intent == request.primary_intent)
            and (request.stage is None or item.development_stage == request.stage)
            and (request.appraisal is None or item.empirical_appraisal == request.appraisal)
            and (request.freshness is None or item.freshness == request.freshness)
        )
        return cast(
            dict[str, JsonValue],
            {
                "hypotheses": [self._summary(item) for item in items],
                "next_cursor": None,
            },
        )

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        item = self._read(request)
        return {
            "hypothesis": item.model_dump(mode="json"),
            "assumptions": [
                value.model_dump(mode="json")
                for value in self._store.list_assumptions(request.project_id, request.hypothesis_id)
            ],
            "predictions": [
                value.model_dump(mode="json")
                for value in self._store.list_predictions(request.project_id, request.hypothesis_id)
            ],
            "appraisals": [
                value.model_dump(mode="json")
                for value in self._store.list_appraisals(request.project_id, request.hypothesis_id)
            ],
        }

    async def graph_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisListInput.model_validate(value)
        nodes = tuple(
            item
            for item in self._store.list_hypotheses(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
            and (request.portfolio_id is None or item.portfolio_id == request.portfolio_id)
        )
        edges = self._store.list_relations(request.project_id, request.portfolio_id)
        return cast(
            dict[str, JsonValue],
            {
                "nodes": [self._summary(item) for item in nodes],
                "edges": [item.model_dump(mode="json") for item in edges],
                "alternative_families": sorted(
                    {item.primary_intent for item in nodes if item.primary_intent} - {"EXPLORATORY"}
                ),
                "unknown_reserve": [
                    item.hypothesis_id for item in nodes if item.primary_intent == "EXPLORATORY"
                ],
            },
        )

    async def portfolio_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_portfolios(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
        )
        return {
            "portfolios": [item.model_dump(mode="json") for item in items],
            "next_cursor": None,
        }

    async def portfolio_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioReadInput.model_validate(value)
        item = self._portfolio(request)
        return {
            "portfolio": item.model_dump(mode="json"),
            "relations": [
                relation.model_dump(mode="json")
                for relation in self._store.list_relations(request.project_id, request.portfolio_id)
            ],
        }

    async def relation_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_relations(request.project_id, request.portfolio_id)
            if (
                request.hypothesis_id is None
                or request.hypothesis_id in {item.source_hypothesis_id, item.target_hypothesis_id}
            )
            and (request.relation_type is None or item.relation_type == request.relation_type)
        )
        return {"relations": [item.model_dump(mode="json") for item in items]}

    async def prediction_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PredictionListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_predictions(request.project_id, request.hypothesis_id)
            if (
                request.prespecification_state is None
                or item.prespecification_state == request.prespecification_state
            )
            and (
                request.observation_bound is None
                or item.observation_bound == request.observation_bound
            )
        )
        return {"predictions": [item.model_dump(mode="json") for item in items]}

    async def prediction_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PredictionReadInput.model_validate(value)
        item = self._store.read_prediction(request.prediction_id)
        if item is None or item.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "prediction not found")
        return {
            "prediction": item.model_dump(mode="json"),
            "test_bindings": [
                binding.model_dump(mode="json")
                for binding in self._store.list_test_bindings(
                    request.project_id, request.prediction_id
                )
            ],
        }

    async def assumption_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AssumptionListInput.model_validate(value)
        items = self._store.list_assumptions(request.project_id, request.hypothesis_id)
        if request.prediction_id is not None:
            prediction = self._store.read_prediction(request.prediction_id)
            allowed = set(() if prediction is None else prediction.assumption_refs)
            items = tuple(item for item in items if item.assumption_id in allowed)
        if request.status is not None:
            items = tuple(item for item in items if item.status == request.status)
        return {"assumptions": [item.model_dump(mode="json") for item in items]}

    async def quality_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        item = self._read(request)
        return cast(
            dict[str, JsonValue],
            {
                "gateway_results": item.gateway_results,
                "epistemic_profile": item.quality_profile,
                "evaluator_identity": "deterministic-gateway+candidate-quality-profile:1",
                "rationale": "axes remain non-compensatory and are never averaged",
                "source_refs": list(item.evidence_refs),
            },
        )

    async def appraisal_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        item = self._read(request)
        appraisals = self._store.list_appraisals(request.project_id, request.hypothesis_id)
        return cast(
            dict[str, JsonValue],
            {
                "hypothesis_revision_digest": item.revision_digest,
                "cumulative_evidence": list(
                    dict.fromkeys(ref for record in appraisals for ref in record.evidence_refs)
                ),
                "test_validity_chain": [
                    binding.model_dump(mode="json")
                    for binding in self._store.list_test_bindings(request.project_id, None)
                    if binding.test_binding_id
                    in {ref for record in appraisals for ref in record.test_assessment_refs}
                ],
                "appraisals": [record.model_dump(mode="json") for record in appraisals],
                "current_scoped_appraisal": item.empirical_appraisal,
            },
        )

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        self._read(request)
        return cast(
            dict[str, JsonValue],
            {
                "records": [
                    record.model_dump(mode="json")
                    for record in self._store.list_audit(request.project_id, request.hypothesis_id)
                ]
            },
        )

    async def generate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = GenerateInput.model_validate(value)
        return generate_hypotheses(
            service=self._service,
            artifacts=self._artifacts,
            project_id=request.project_id,
            object_id=request.object_id,
            portfolio_id=request.portfolio_id,
            question=request.question,
            evidence_scope=request.evidence_scope,
            intent_hints=request.intent_hints,
            generation_policy_ref=request.generation_policy_ref,
            budget_policy_ref=request.budget_policy_ref,
        )

    async def create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CreateInput.model_validate(value)
        try:
            item, duplicates, commit = self._service.create(
                project_id=request.project_id,
                object_id=request.object_id,
                portfolio_id=request.portfolio_id,
                statement=request.statement,
                primary_intent=request.primary_intent,
                secondary_intents=request.secondary_intents,
                evidence_basis=request.evidence_basis,
                scope=request.scope,
                evidence_refs=request.evidence_refs,
                prespecification_state=request.prespecification_state,
                expected_object_revision=request.expected_object_revision,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "hypothesis": item.model_dump(mode="json"),
                "gateway_result": item.gateway_results,
                "duplicate_candidates": list(duplicates),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReviseInput.model_validate(value)
        current = self._read_expected(request)
        allowed = {
            "statement",
            "observed_problem",
            "evidence_basis",
            "scope",
            "evidence_refs",
            "counterevidence_refs",
            "counterevidence_queries",
            "prespecification_state",
        }
        rejected = sorted(set(request.patch) - allowed)
        if rejected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                f"noncanonical hypothesis patch: {', '.join(rejected)}",
            )
        updates: dict[str, object] = {
            key: tuple(child) if key.endswith("_refs") and isinstance(child, list) else child
            for key, child in request.patch.items()
        }
        revised, commit = self._revise(
            current,
            updates=updates,
            event_type="hypothesis/updated",
            evidence_refs=request.evidence_refs,
            invalidate_derived=True,
        )
        return self._revision_result(current, revised, commit)

    async def intent_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = IntentUpdateInput.model_validate(value)
        current = self._read_expected(request)
        if request.primary_intent not in PRIMARY_INTENTS or any(
            child not in PRIMARY_INTENTS for child in request.secondary_intents
        ):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "unsupported intent")
        revised, commit = self._revise(
            current,
            updates={
                "primary_intent": request.primary_intent,
                "secondary_intents": request.secondary_intents,
                "intent_profile_refs": request.intent_profile_refs,
                "causal_profile": (
                    {"applicability": "REQUIRED", "primary_locus": "UNCLASSIFIED"}
                    if request.primary_intent in CAUSAL_INTENTS
                    else {"applicability": "NOT_APPLICABLE"}
                ),
            },
            event_type="hypothesis/intentChanged",
            evidence_refs=request.evidence_refs,
            invalidate_derived=True,
        )
        result = self._revision_result(current, revised, commit)
        result["gateway_profile_delta"] = cast(
            JsonValue,
            {
                "before": current.primary_intent,
                "after": revised.primary_intent,
            },
        )
        return result

    async def causal_update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CausalUpdateInput.model_validate(value)
        current = self._read_expected(request)
        if current.primary_intent not in CAUSAL_INTENTS:
            causal: dict[str, object] = {"applicability": "NOT_APPLICABLE"}
        else:
            allowed = {
                "primary_locus",
                "contributing_loci",
                "causal_depth",
                "lifecycle",
                "mechanism",
                "validity_blockers",
            }
            rejected = set(request.causal_patch) - allowed
            if rejected:
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED,
                    f"unsupported causal facet: {', '.join(sorted(rejected))}",
                )
            causal = {"applicability": "APPLICABLE", **request.causal_patch}
        revised, commit = self._revise(
            current,
            updates={"causal_profile": causal},
            event_type="hypothesis/updated",
            evidence_refs=request.evidence_refs,
            invalidate_derived=True,
        )
        return self._revision_result(current, revised, commit)

    async def relation_add(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationAddInput.model_validate(value)
        portfolio = self._portfolio_expected(
            request.project_id,
            request.portfolio_id,
            request.expected_portfolio_revision,
        )
        try:
            relation, revised, commit = self._service.add_relation(
                portfolio,
                source_hypothesis_id=request.source_hypothesis_id,
                relation_type=request.relation_type,
                target_hypothesis_id=request.target_hypothesis_id,
                evidence_refs=request.evidence_refs,
                semantic_role=request.semantic_role,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "relation": relation.model_dump(mode="json"),
                "portfolio": revised.model_dump(mode="json"),
                "quality_impact": "REVALIDATION_REQUIRED",
                "discrimination_impact": "REVALIDATION_REQUIRED",
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def relation_remove(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationRemoveInput.model_validate(value)
        relation = self._store.read_relation(request.relation_id)
        if relation is None or relation.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "relation not found")
        portfolio = self._portfolio_expected(
            request.project_id,
            relation.portfolio_id,
            request.expected_portfolio_revision,
        )
        try:
            ended, revised, commit = self._service.end_relation(
                portfolio,
                relation,
                reason=request.reason,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "relation": ended.model_dump(mode="json"),
                "portfolio": revised.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def assumption_add(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AssumptionAddInput.model_validate(value)
        current = self._read_expected(request)
        try:
            assumption, revised, commit = self._service.add_assumption(
                current,
                statement=request.statement,
                role=request.role,
                evidence_refs=request.evidence_refs,
                validation_route=request.validation_route,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "assumption": assumption.model_dump(mode="json"),
            "hypothesis": revised.model_dump(mode="json"),
            "prediction_test_impact": list(revised.invalidated_refs),
            "commit": commit.model_dump(mode="json"),
        }

    async def prediction_bind(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PredictionBindInput.model_validate(value)
        current = self._store.read_hypothesis(
            request.project_id,
            request.hypothesis_id,
            request.hypothesis_revision_digest,
        )
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "hypothesis revision not found")
        try:
            prediction, revised, commit = self._service.bind_prediction(
                current,
                knowledge_cutoff=request.knowledge_cutoff,
                prespecification_state=request.prespecification_state,
                conditions=request.conditions,
                measurement_contract_ref=request.measurement_contract_ref,
                assumption_refs=request.assumption_refs,
                expected_outcome=cast(dict[str, object], request.expected_outcome),
                discrimination_map=cast(dict[str, object], request.discrimination_map),
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "prediction": prediction.model_dump(mode="json"),
            "hypothesis": revised.model_dump(mode="json"),
            "leakage_check": "PASS",
            "commit": commit.model_dump(mode="json"),
        }

    async def counterevidence_request(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CounterevidenceRequestInput.model_validate(value)
        current = self._read(request)
        object_value = self._objects.read_object(request.project_id, current.object_id, None)
        thread = None if object_value is None else self._threads.read(object_value.thread_id)
        if thread is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "hypothesis thread not found")
        with self._service.transaction():
            investigation = self._investigations.start(
                thread=thread,
                trigger="COUNTER_SEARCH",
                question=f"Find evidence against hypothesis: {current.statement}",
                target_object_id=current.object_id,
                mode="CRITICAL",
                scope={"hypothesis_id": current.hypothesis_id},
                required_evidence_groups=("counterevidence", "alternative explanation"),
                query_families=request.source_scope or ("project sources",),
                stop_conditions=("SUFFICIENT_COUNTEREVIDENCE", "DIMINISHING_INFORMATION_VALUE"),
            )
            revised, commit = self._revise(
                current,
                updates={
                    "counterevidence_queries": (
                        *current.counterevidence_queries,
                        investigation.investigation_id,
                    ),
                    "development_stage": "COUNTEREVIDENCE_CHECKED",
                },
                event_type="hypothesis/counterevidenceRequested",
            )
            return {
                "investigation": investigation.model_dump(mode="json"),
                "counter_search_contract": {
                    "source_scope": list(request.source_scope),
                    "budget_policy_ref": request.budget_policy_ref,
                    "stop_conditions": list(investigation.stop_conditions),
                },
                "hypothesis": revised.model_dump(mode="json"),
                "commit": commit.model_dump(mode="json"),
            }

    async def portfolio_compose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioComposeInput.model_validate(value)
        try:
            portfolio, commit = self._service.compose_portfolio(
                project_id=request.project_id,
                object_id=request.object_id,
                portfolio_id=request.portfolio_id,
                hypothesis_ids=request.hypothesis_ids,
                unknown_reserve=cast(dict[str, object], request.unknown_reserve),
                expected_object_revision=request.expected_object_revision,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "portfolio": portfolio.model_dump(mode="json"),
                "duplicate_candidates": [],
                "quality_gaps": list(portfolio.quality_gaps),
                "discrimination_matrix_candidate": cast(
                    JsonValue, list(portfolio.discrimination_matrix)
                ),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def portfolio_revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioRevalidateInput.model_validate(value)
        current = self._portfolio(request)
        try:
            revised, commit = self._service.revalidate_portfolio(current, request.trigger_reason)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "portfolio": revised.model_dump(mode="json"),
                "coverage": revised.coverage_state,
                "diversity": revised.diversity_state,
                "discrimination": revised.discrimination_state,
                "unknown_abstention": revised.abstention_state,
                "invalidations": list(revised.quality_gaps),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def test_bind(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = TestBindInput.model_validate(value)
        prediction = self._store.read_prediction(request.prediction_id)
        if prediction is None or prediction.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "prediction not found")
        try:
            binding = self._service.bind_test(
                prediction,
                execution_ref=request.execution_ref,
                observation_refs=request.observation_refs,
                test_validity_assessment_ref=request.test_validity_assessment_ref,
                test_validity=request.test_validity,
                prediction_fit=request.prediction_fit,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "test_binding": binding.model_dump(mode="json"),
            "appraisal_update_allowed": binding.appraisal_mutation_allowed,
            "affected_portfolio": None,
        }

    async def appraise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AppraiseInput.model_validate(value)
        current = self._read(request)
        try:
            appraisal, revised, commit = self._service.appraise(
                current,
                evidence_refs=request.evidence_refs,
                test_assessment_refs=request.test_assessment_refs,
                appraisal_scope=request.appraisal_scope,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "appraisal": appraisal.model_dump(mode="json"),
            "hypothesis": revised.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    async def appraise_quality(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        current = self._read(request)
        quality = dict(current.quality_profile)
        quality.update(
            {
                "counterevidence_readiness": (
                    "STRONG" if current.counterevidence_queries else "WEAK"
                ),
                "prediction_specificity": ("STRONG" if current.prediction_refs else "WEAK"),
                "testability": "STRONG" if current.test_refs else "MIXED",
                "discrimination": ("MIXED" if current.prediction_refs else "NOT_ASSESSED"),
            }
        )
        revised, commit = self._revise(
            current,
            updates={"quality_profile": quality},
            event_type="hypothesis/qualityUpdated",
        )
        return cast(
            dict[str, JsonValue],
            {
                "hypothesis": revised.model_dump(mode="json"),
                "epistemic_profile": quality,
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def split_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SplitProposeInput.model_validate(value)
        current = self._read_expected(request)
        payload: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "hypothesis_id": request.hypothesis_id,
            "subhypotheses": [dict(item) for item in request.subhypotheses],
            "evidence_refs": list(request.evidence_refs),
            "rationale": request.rationale,
            "expected_revision_digest": request.expected_revision_digest,
            "applied": False,
            "owner_namespace": "REVISION",
        }
        digest = domain_digest("HYPOTHESIS_SPLIT_PROPOSAL", "1.0.0", canonical_payload(payload))
        self._service.audit(
            request.project_id,
            current.hypothesis_id,
            "hypothesis/splitProposed",
            {**payload, "digest": digest},
        )
        return {
            "split_proposal": {**payload, "digest": digest},
            "lineage_preview": [f"SPECIALIZES:{current.hypothesis_id}"],
            "portfolio_impact_preview": [current.portfolio_id],
        }

    async def merge_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeProposeInput.model_validate(value)
        if len(request.hypothesis_ids) != len(request.expected_revision_digests):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "hypothesis IDs and expected revision digests must align",
            )
        items = tuple(
            self._store.read_hypothesis(request.project_id, item, None)
            for item in request.hypothesis_ids
        )
        if any(item is None for item in items):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "hypothesis not found")
        actual = tuple(item.revision_digest for item in items if item is not None)
        if actual != request.expected_revision_digests:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision mismatch")
        statements = {item.statement for item in items if item is not None}
        conflicts = [] if len(statements) == 1 else ["statement"]
        payload: dict[str, JsonValue] = {
            "project_id": request.project_id,
            "hypothesis_ids": list(request.hypothesis_ids),
            "field_mapping": request.field_mapping,
            "evidence_refs": list(request.evidence_refs),
            "rationale": request.rationale,
            "expected_revision_digests": list(request.expected_revision_digests),
            "semantic_conflicts": cast(JsonValue, conflicts),
            "applied": False,
            "owner_namespace": "REVISION",
        }
        digest = domain_digest("HYPOTHESIS_MERGE_PROPOSAL", "1.0.0", canonical_payload(payload))
        first = cast(HypothesisRecord, items[0])
        self._service.audit(
            request.project_id,
            first.hypothesis_id,
            "hypothesis/mergeProposed",
            {**payload, "digest": digest},
        )
        return {
            "merge_proposal": {**payload, "digest": digest},
            "lineage_preview": [
                f"SUPERSEDES:{hypothesis_id}" for hypothesis_id in request.hypothesis_ids
            ],
            "portfolio_impact_preview": [first.portfolio_id],
        }

    def _read(self, request: HypothesisReadInput) -> HypothesisRecord:
        item = self._store.read_hypothesis(
            request.project_id, request.hypothesis_id, request.revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "hypothesis not found")
        return item

    def _read_expected(self, request: RevisionBoundInput) -> HypothesisRecord:
        item = self._store.read_hypothesis(request.project_id, request.hypothesis_id, None)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "hypothesis not found")
        if item.revision_digest != request.expected_revision_digest:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "hypothesis revision changed concurrently"
            )
        return item

    def _portfolio(self, request: PortfolioReadInput) -> HypothesisPortfolioRecord:
        item = self._store.read_portfolio(
            request.project_id, request.portfolio_id, request.revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "portfolio not found")
        return item

    def _portfolio_expected(
        self, project_id: str, portfolio_id: str, expected: str
    ) -> HypothesisPortfolioRecord:
        item = self._store.read_portfolio(project_id, portfolio_id, None)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "portfolio not found")
        if item.revision_digest != expected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "portfolio revision changed concurrently"
            )
        return item

    def _revise(
        self,
        current: HypothesisRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
        invalidate_derived: bool = False,
    ) -> tuple[HypothesisRecord, CommitResult]:
        try:
            return self._service.revise(
                current,
                updates=updates,
                event_type=event_type,
                evidence_refs=evidence_refs,
                invalidate_derived=invalidate_derived,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    @staticmethod
    def _summary(item: HypothesisRecord) -> dict[str, JsonValue]:
        return {
            "hypothesis_id": item.hypothesis_id,
            "object_id": item.object_id,
            "portfolio_id": item.portfolio_id,
            "statement": item.statement,
            "primary_intent": item.primary_intent,
            "intent_profile_refs": list(item.intent_profile_refs),
            "development_stage": item.development_stage,
            "empirical_appraisal": item.empirical_appraisal,
            "freshness": item.freshness,
            "revision_digest": item.revision_digest,
        }

    @staticmethod
    def _revision_result(
        before: HypothesisRecord, after: HypothesisRecord, commit: CommitResult
    ) -> dict[str, JsonValue]:
        before_data = before.model_dump(mode="json")
        after_data = after.model_dump(mode="json")
        diff = [
            {"path": key, "before": before_data.get(key), "after": after_data.get(key)}
            for key in sorted(set(before_data) | set(after_data))
            if before_data.get(key) != after_data.get(key)
        ]
        return cast(
            dict[str, JsonValue],
            {
                "hypothesis": after_data,
                "semantic_diff": diff,
                "invalidated_predictions_tests_appraisals": list(after.invalidated_refs),
                "stage": after.development_stage,
                "freshness": after.freshness,
                "commit": commit.model_dump(mode="json"),
            },
        )
