from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.outcome_service import OutcomeService
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.outcome_full import OutcomeAssessmentRecord, OutcomeSeriesRecord
from thoth.ports.outcome import OutcomeStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class OutcomeListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    action_id: str | None = Field(default=None, max_length=160)
    plan_execution_id: str | None = Field(default=None, max_length=160)
    lifecycle: str | None = Field(default=None, max_length=80)
    validity: str | None = Field(default=None, max_length=80)
    phase: str | None = Field(default=None, max_length=80)


class OutcomeReadInput(ProjectInput):
    outcome_assessment_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class SeriesListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    action_plan_id: str | None = Field(default=None, max_length=160)


class SeriesReadInput(ProjectInput):
    outcome_series_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class ProfileListInput(ProjectInput):
    action_purpose: str | None = Field(default=None, max_length=80)
    enabled_only: bool = True


class ProfileReadInput(ProjectInput):
    profile_ref: str = Field(min_length=1, max_length=160)
    version: int | None = Field(default=None, ge=1)


class AttributionReadInput(ProjectInput):
    attribution_assessment_id: str = Field(min_length=1, max_length=160)


class ChangeSetReadInput(ProjectInput):
    outcome_change_set_id: str = Field(min_length=1, max_length=160)


class ImpactReadInput(ProjectInput):
    impact_assessment_id: str = Field(min_length=1, max_length=160)


class AuditReadInput(ProjectInput):
    outcome_series_id: str | None = Field(default=None, max_length=160)
    outcome_assessment_id: str | None = Field(default=None, max_length=160)


class SeriesCreateInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    action_plan_revision_digest: str = Field(min_length=64, max_length=64)
    planned_execution_ref: str | None = Field(default=None, max_length=160)
    profile_ref: str = Field(min_length=1, max_length=160)
    comparison_baseline_set_digest: str = Field(min_length=64, max_length=64)
    assessment_windows: tuple[dict[str, JsonValue], ...]


class ObservationLinkInput(ProjectInput):
    outcome_series_id: str = Field(min_length=1, max_length=160)
    assessment_phase: str = Field(pattern=r"^(INTERIM|FOLLOW_UP|FINAL_WITHIN_SCOPE)$")
    observation_refs: tuple[str, ...]
    completeness: str = Field(pattern=r"^(COMPLETE|PARTIAL|MISSING|CORRUPT|NOT_APPLICABLE)$")
    evidence_refs: tuple[str, ...]
    expected_series_revision: int = Field(ge=0)


class AssessInput(ProjectInput):
    outcome_series_id: str = Field(min_length=1, max_length=160)
    assessment_phase: str = Field(pattern=r"^(INTERIM|FOLLOW_UP|FINAL_WITHIN_SCOPE)$")
    profile_version: int = Field(ge=1)
    observation_refs: tuple[str, ...]
    comparator_refs: tuple[str, ...]
    assumptions: tuple[str, ...]
    expected_series_revision: int = Field(ge=0)


class ReassessInput(OutcomeReadInput):
    new_evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class AttributionAssessInput(ProjectInput):
    outcome_assessment_id: str = Field(min_length=1, max_length=160)
    attribution_method: str = Field(min_length=1, max_length=160)
    contextual_factor_refs: tuple[str, ...]
    counterfactual_evidence_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...]


class ChangeSetProposeInput(ProjectInput):
    outcome_assessment_id: str = Field(min_length=1, max_length=160)
    proposed_entity_changes: dict[str, JsonValue]
    impact_policy_ref: str = Field(min_length=1, max_length=160)
    expected_project_head_set: str = Field(min_length=64, max_length=64)


class FollowupGenerateInput(ProjectInput):
    outcome_assessment_id: str = Field(min_length=1, max_length=160)
    follow_up_scope: str = Field(min_length=1, max_length=2_000)
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class ImpactProposeInput(ProjectInput):
    outcome_series_id: str = Field(min_length=1, max_length=160)
    broader_window: str = Field(min_length=1, max_length=500)
    impact_profile_ref: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]
    attribution_design_ref: str = Field(min_length=1, max_length=160)


class OutcomeHandlers:
    def __init__(self, *, store: OutcomeStorePort, service: OutcomeService) -> None:
        self._store = store
        self._service = service

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OutcomeListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_assessments(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
            and (
                request.plan_execution_id is None or item.execution_ref == request.plan_execution_id
            )
            and (request.lifecycle is None or item.lifecycle == request.lifecycle)
            and (request.validity is None or item.validity == request.validity)
            and (request.phase is None or item.assessment_phase == request.phase)
        )
        return cast(
            dict[str, JsonValue],
            {
                "outcomes": [
                    {
                        "outcome_assessment_id": item.outcome_assessment_id,
                        "outcome_series_id": item.outcome_series_id,
                        "object_id": item.object_id,
                        "assessment_phase": item.assessment_phase,
                        "lifecycle": item.lifecycle,
                        "validity": item.validity,
                        "objective_attainment": item.objective_attainment,
                        "attribution_state": item.attribution_state,
                        "follow_up_state": item.follow_up_state,
                        "revision_digest": item.revision_digest,
                    }
                    for item in items
                ],
                "next_cursor": None,
            },
        )

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = OutcomeReadInput.model_validate(value)
        return {"outcome": self._assessment(request).model_dump(mode="json")}

    async def series_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SeriesListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_series(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
        )
        return {
            "series": [item.model_dump(mode="json") for item in items],
            "next_cursor": None,
        }

    async def series_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SeriesReadInput.model_validate(value)
        return {"series": self._series(request).model_dump(mode="json")}

    async def profile_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileListInput.model_validate(value)
        items = self._store.list_profiles(request.enabled_only)
        if request.action_purpose is not None:
            items = tuple(item for item in items if request.action_purpose in item.action_purposes)
        return {"profiles": [item.model_dump(mode="json") for item in items]}

    async def profile_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProfileReadInput.model_validate(value)
        item = self._store.read_profile(request.profile_ref, request.version)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "profile not found")
        return {"profile": item.model_dump(mode="json")}

    async def attribution_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttributionReadInput.model_validate(value)
        item = self._store.read_attribution(request.project_id, request.attribution_assessment_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "attribution not found")
        return {"attribution": item.model_dump(mode="json")}

    async def change_set_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetReadInput.model_validate(value)
        item = self._store.read_change_set(request.project_id, request.outcome_change_set_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "ChangeSet not found")
        return {"change_set": item.model_dump(mode="json")}

    async def impact_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImpactReadInput.model_validate(value)
        item = self._store.read_impact(request.project_id, request.impact_assessment_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "impact not found")
        return {"impact": item.model_dump(mode="json")}

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuditReadInput.model_validate(value)
        subject = request.outcome_assessment_id or request.outcome_series_id
        return {
            "records": [
                item.model_dump(mode="json")
                for item in self._store.list_audit(request.project_id, subject)
            ]
        }

    async def series_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SeriesCreateInput.model_validate(value)
        try:
            series, missing, commit = self._service.create_series(
                project_id=request.project_id,
                object_id=request.object_id,
                action_plan_revision_digest=request.action_plan_revision_digest,
                planned_execution_ref=request.planned_execution_ref,
                profile_ref=request.profile_ref,
                comparison_baseline_set_digest=request.comparison_baseline_set_digest,
                assessment_windows=tuple(
                    cast(dict[str, object], item) for item in request.assessment_windows
                ),
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "series": series.model_dump(mode="json"),
                "required_evidence_windows": list(series.assessment_windows),
                "missing_fields": list(missing),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def observation_link(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ObservationLinkInput.model_validate(value)
        current = self._series_expected(
            request.project_id,
            request.outcome_series_id,
            request.expected_series_revision,
        )
        try:
            series, commit = self._service.link_observations(
                current,
                phase=request.assessment_phase,
                observation_refs=request.observation_refs,
                completeness=request.completeness,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "series": series.model_dump(mode="json"),
            "assessment_state": series.phase_states[request.assessment_phase],
            "source_validation": "PASS",
            "commit": commit.model_dump(mode="json"),
        }

    async def assess(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AssessInput.model_validate(value)
        current = self._series_expected(
            request.project_id,
            request.outcome_series_id,
            request.expected_series_revision,
        )
        try:
            assessment, series, assessment_commit, series_commit = self._service.assess(
                current,
                phase=request.assessment_phase,
                profile_version=request.profile_version,
                observation_refs=request.observation_refs,
                comparator_refs=request.comparator_refs,
                assumptions=request.assumptions,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "assessment": assessment.model_dump(mode="json"),
            "series": series.model_dump(mode="json"),
            "commits": [
                assessment_commit.model_dump(mode="json"),
                series_commit.model_dump(mode="json"),
            ],
        }

    async def reassess(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReassessInput.model_validate(value)
        current = self._store.read_assessment(
            request.project_id,
            request.outcome_assessment_id,
            request.expected_revision_digest,
        )
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "assessment revision not found")
        try:
            assessment, commit = self._service.reassess(
                current,
                new_evidence_refs=request.new_evidence_refs,
                reason=request.reason,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "assessment": assessment.model_dump(mode="json"),
            "prior_assessment_preserved": True,
            "changed_dimensions": list(assessment.changed_dimensions),
            "dependent_impact": "REVALIDATION_REQUIRED",
            "commit": commit.model_dump(mode="json"),
        }

    async def attribution_assess(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AttributionAssessInput.model_validate(value)
        current = self._assessment(
            OutcomeReadInput(
                project_id=request.project_id,
                outcome_assessment_id=request.outcome_assessment_id,
            )
        )
        try:
            attribution, assessment, commit = self._service.assess_attribution(
                current,
                method=request.attribution_method,
                contextual_factor_refs=request.contextual_factor_refs,
                counterfactual_evidence_refs=request.counterfactual_evidence_refs,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "attribution": attribution.model_dump(mode="json"),
            "assessment": assessment.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        }

    async def change_set_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ChangeSetProposeInput.model_validate(value)
        current = self._assessment(
            OutcomeReadInput(
                project_id=request.project_id,
                outcome_assessment_id=request.outcome_assessment_id,
            )
        )
        record = self._service.propose_change_set(
            current,
            proposed_entity_changes=cast(dict[str, object], request.proposed_entity_changes),
            impact_policy_ref=request.impact_policy_ref,
            expected_project_head_set=request.expected_project_head_set,
        )
        return {
            "change_set": record.model_dump(mode="json"),
            "current_state_mutated": False,
        }

    async def followup_generate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = FollowupGenerateInput.model_validate(value)
        current = self._assessment(
            OutcomeReadInput(
                project_id=request.project_id,
                outcome_assessment_id=request.outcome_assessment_id,
            )
        )
        payload: dict[str, JsonValue] = {
            "outcome_assessment_id": current.outcome_assessment_id,
            "follow_up_scope": request.follow_up_scope,
            "budget_policy_ref": request.budget_policy_ref,
            "investigation_candidate": {
                "question": "resolve Outcome limitations and comparator gaps",
                "automatic_execution": False,
            },
            "action_candidate": {
                "purpose": "INFORMATION_ACQUISITION",
                "automatic_execution": False,
            },
            "compensation_candidate": (
                {"required": True, "automatic_execution": False}
                if current.follow_up_state == "COMPENSATION_REQUIRED"
                else {"required": False, "automatic_execution": False}
            ),
        }
        digest = domain_digest("OUTCOME_FOLLOWUP_CANDIDATES", "1.0.0", canonical_payload(payload))
        self._service.audit(
            request.project_id,
            current.outcome_assessment_id,
            "outcome/followupGenerated",
            {**payload, "digest": digest},
        )
        return {"candidates": {**payload, "digest": digest}}

    async def impact_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImpactProposeInput.model_validate(value)
        series = self._series(
            SeriesReadInput(
                project_id=request.project_id,
                outcome_series_id=request.outcome_series_id,
            )
        )
        try:
            impact = self._service.propose_impact(
                series,
                broader_window=request.broader_window,
                impact_profile_ref=request.impact_profile_ref,
                evidence_refs=request.evidence_refs,
                attribution_design_ref=request.attribution_design_ref,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "impact": impact.model_dump(mode="json"),
            "limitations": list(impact.limitations),
        }

    def _series(self, request: SeriesReadInput) -> OutcomeSeriesRecord:
        item = self._store.read_series(
            request.project_id, request.outcome_series_id, request.revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "series not found")
        return item

    def _series_expected(
        self, project_id: str, series_id: str, revision: int
    ) -> OutcomeSeriesRecord:
        item = self._store.read_series(project_id, series_id, None)
        if item is None or item.revision != revision:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "OutcomeSeries revision changed"
            )
        return item

    def _assessment(self, request: OutcomeReadInput) -> OutcomeAssessmentRecord:
        item = self._store.read_assessment(
            request.project_id,
            request.outcome_assessment_id,
            request.revision_digest,
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "assessment not found")
        return item
