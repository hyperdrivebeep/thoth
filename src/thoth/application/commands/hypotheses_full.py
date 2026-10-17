from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.hypothesis_detail_queries import (
    appraisal_detail,
    assumption_list_result,
    hypothesis_audit_result,
    hypothesis_detail,
    portfolio_detail,
    portfolio_list_result,
    prediction_detail,
)
from thoth.application.commands.hypothesis_generation import generate_hypotheses
from thoth.application.commands.hypothesis_inputs import (
    AppraiseInput,
    AssumptionAddInput,
    AssumptionListInput,
    CausalUpdateInput,
    CounterevidenceRequestInput,
    CreateInput,
    GenerateInput,
    HypothesisListInput,
    HypothesisReadInput,
    IntentUpdateInput,
    MergeProposeInput,
    PortfolioComposeInput,
    PortfolioListInput,
    PortfolioReadInput,
    PortfolioRevalidateInput,
    PredictionBindInput,
    PredictionListInput,
    PredictionReadInput,
    RelationAddInput,
    RelationListInput,
    RelationRemoveInput,
    ReviseInput,
    RevisionBoundInput,
    SplitProposeInput,
    TestBindInput,
)
from thoth.application.commands.hypothesis_mutation_projection import (
    added_assumption_result,
    added_relation_result,
    appraised_hypothesis_result,
    appraised_quality_result,
    bound_prediction_result,
    bound_test_result,
    composed_portfolio_result,
    created_hypothesis_result,
    intent_update_payload,
    quality_update_payload,
    removed_relation_result,
    revalidated_portfolio_result,
)
from thoth.application.commands.hypothesis_proposal_projection import (
    merge_proposal_data,
    merge_proposal_result,
    split_proposal_data,
    split_proposal_result,
)
from thoth.application.commands.hypothesis_read_projection import (
    hypothesis_graph_matches,
    hypothesis_graph_result,
    hypothesis_matches,
    hypothesis_prediction_matches,
    hypothesis_quality_result,
    hypothesis_relation_matches,
    hypothesis_revision_result,
    hypothesis_summary,
)
from thoth.application.services.hypothesis_service import (
    CAUSAL_INTENTS,
    PRIMARY_INTENTS,
    HypothesisService,
)
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


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
            if hypothesis_matches(item, request)
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
        return hypothesis_detail(request, item, self._store)

    async def graph_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisListInput.model_validate(value)
        nodes = tuple(
            item
            for item in self._store.list_hypotheses(request.project_id)
            if hypothesis_graph_matches(item, request)
        )
        edges = self._store.list_relations(request.project_id, request.portfolio_id)
        return hypothesis_graph_result(nodes, edges, [self._summary(item) for item in nodes])

    async def portfolio_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioListInput.model_validate(value)
        return portfolio_list_result(request, self._store)

    async def portfolio_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioReadInput.model_validate(value)
        item = self._portfolio(request)
        return portfolio_detail(request, item, self._store)

    async def relation_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RelationListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_relations(request.project_id, request.portfolio_id)
            if hypothesis_relation_matches(item, request)
        )
        return {"relations": [item.model_dump(mode="json") for item in items]}

    async def prediction_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PredictionListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_predictions(request.project_id, request.hypothesis_id)
            if hypothesis_prediction_matches(item, request)
        )
        return {"predictions": [item.model_dump(mode="json") for item in items]}

    async def prediction_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PredictionReadInput.model_validate(value)
        item = self._store.read_prediction(request.prediction_id)
        if item is None or item.project_id != request.project_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "prediction not found")
        return prediction_detail(request, item, self._store)

    async def assumption_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AssumptionListInput.model_validate(value)
        return assumption_list_result(request, self._store)

    async def quality_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        item = self._read(request)
        return hypothesis_quality_result(item)

    async def appraisal_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        item = self._read(request)
        appraisals = self._store.list_appraisals(request.project_id, request.hypothesis_id)
        return appraisal_detail(request, item, appraisals, self._store)

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        self._read(request)
        return hypothesis_audit_result(request, self._store)

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
        return created_hypothesis_result(item, duplicates, commit)

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
            updates=intent_update_payload(request, CAUSAL_INTENTS),
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
        return added_relation_result(relation, revised, commit)

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
        return removed_relation_result(ended, revised, commit)

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
        return added_assumption_result(assumption, revised, commit)

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
        return bound_prediction_result(prediction, revised, commit)

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
        return composed_portfolio_result(portfolio, commit)

    async def portfolio_revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioRevalidateInput.model_validate(value)
        current = self._portfolio(request)
        try:
            revised, commit = self._service.revalidate_portfolio(current, request.trigger_reason)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return revalidated_portfolio_result(revised, commit)

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
        return bound_test_result(binding)

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
        return appraised_hypothesis_result(appraisal, revised, commit)

    async def appraise_quality(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = HypothesisReadInput.model_validate(value)
        current = self._read(request)
        quality = quality_update_payload(current)
        revised, commit = self._revise(
            current,
            updates={"quality_profile": quality},
            event_type="hypothesis/qualityUpdated",
        )
        return appraised_quality_result(revised, quality, commit)

    async def split_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SplitProposeInput.model_validate(value)
        current = self._read_expected(request)
        payload, digest = split_proposal_data(request)
        self._service.audit(
            request.project_id,
            current.hypothesis_id,
            "hypothesis/splitProposed",
            {**payload, "digest": digest},
        )
        return split_proposal_result(payload, digest, current)

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
        payload, digest = merge_proposal_data(request, items)
        first = cast(HypothesisRecord, items[0])
        self._service.audit(
            request.project_id,
            first.hypothesis_id,
            "hypothesis/mergeProposed",
            {**payload, "digest": digest},
        )
        return merge_proposal_result(request, payload, digest, first)

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

    _summary = staticmethod(hypothesis_summary)

    _revision_result = staticmethod(hypothesis_revision_result)
