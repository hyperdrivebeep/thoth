from __future__ import annotations

import re

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import (
    CriterionProfileDecision,
    CriterionProfileDecisionState,
    CriterionProfileRecord,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.criterion_profile import (
    CriterionProfileCandidateMapperPort,
    CriterionProfileRouterPort,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{3,}")
_WEAK_TERMS = frozenset({"analysis", "performance", "test", "verification", "rnd", "general"})


class SourceGroundedCriterionProfileRouter(CriterionProfileRouterPort):
    def __init__(
        self,
        *,
        profiles: CriterionContractStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        general_profile_ref: str,
        candidate_mapper: CriterionProfileCandidateMapperPort | None = None,
    ) -> None:
        self._profiles = profiles
        self._clock = clock
        self._ids = ids
        self._general_profile_ref = general_profile_ref
        self._candidate_mapper = candidate_mapper

    def route(
        self,
        *,
        project_id: str,
        evidence: tuple[EvidenceSpan, ...],
    ) -> CriterionProfileDecision:
        general = self._profiles.read_profile(self._general_profile_ref, None)
        if general is None or not general.enabled:
            raise ValueError("general Criterion Profile is unavailable")
        source_tokens = self._tokens("\n".join(item.exact_text for item in evidence))
        scores: dict[str, int] = {}
        applicability: dict[str, tuple[str, ...]] = {}
        candidates: list[CriterionProfileRecord] = []
        available = self._profiles.list_profiles(enabled_only=True)
        mapped_refs = (
            ()
            if self._candidate_mapper is None
            else self._candidate_mapper.propose_profile_refs(
                project_id=project_id,
                evidence=evidence,
                available_profile_refs=tuple(item.profile_ref for item in available),
            )
        )
        for profile in available:
            if profile.profile_ref == self._general_profile_ref:
                continue
            terms = self._tokens(" ".join(profile.applicability_terms)) - _WEAK_TERMS
            matched = tuple(sorted(source_tokens.intersection(terms)))
            score = len(matched)
            scores[profile.profile_ref] = score
            applicability[profile.profile_ref] = tuple(
                [f"TERM:{item}" for item in matched]
                + [f"SOURCE_SPAN:{item.span_id}" for item in evidence if matched]
            )
            model_proposed = profile.profile_ref in mapped_refs
            if score >= 2 or (model_proposed and score >= 1):
                candidates.append(profile)
        candidates.sort(key=lambda item: (-scores[item.profile_ref], item.profile_ref))
        changes = {
            profile.profile_ref: self._decision_changes(general, profile) for profile in candidates
        }
        decision_required = any(changes.values())
        selected = () if decision_required else (general.profile_ref,)
        state = (
            CriterionProfileDecisionState.PROFILE_DECISION_REQUIRED
            if decision_required
            else CriterionProfileDecisionState.SELECTED
        )
        now = self._clock.now()
        draft: dict[str, object] = {
            "decision_id": self._ids.new("criterion-profile-decision"),
            "project_id": project_id,
            "state": state.value,
            "selected_profile_refs": selected,
            "candidate_profile_refs": tuple(item.profile_ref for item in candidates),
            "source_span_refs": tuple(item.span_id for item in evidence),
            "source_scores": dict(sorted(scores.items())),
            "decision_dimension_changes": changes,
            "applicability_evidence": {key: applicability[key] for key in sorted(changes)},
            "profile_decision_required": decision_required,
            "hold_reason": (
                "PROFILE_CHANGES_EVIDENCE_EVALUATOR_AUTHORITY_OR_EXIT_CRITERION"
                if decision_required
                else None
            ),
            "created_at": now,
            "schema_version": "1.0.0",
        }
        return CriterionProfileDecision.model_validate(
            {
                **draft,
                "decision_digest": domain_digest(
                    "CRITERION_PROFILE_DECISION",
                    "1.0.0",
                    canonical_payload(draft),
                ),
            }
        )

    @staticmethod
    def _decision_changes(
        general: CriterionProfileRecord,
        candidate: CriterionProfileRecord,
    ) -> tuple[str, ...]:
        changes: list[str] = []
        if (
            general.required_fields != candidate.required_fields
            or general.required_evidence != candidate.required_evidence
            or general.entry_criteria != candidate.entry_criteria
        ):
            changes.append("REQUIRED_EVIDENCE")
        if (
            general.allowed_computation_types != candidate.allowed_computation_types
            or general.measurement_validity != candidate.measurement_validity
        ):
            changes.append("EVALUATOR")
        if (
            general.authority_policy != candidate.authority_policy
            or general.change_authority != candidate.change_authority
        ):
            changes.append("AUTHORITY")
        if (
            general.exit_criteria != candidate.exit_criteria
            or general.decision_rules != candidate.decision_rules
            or general.uncertainty_policy != candidate.uncertainty_policy
        ):
            changes.append("EXIT_CRITERION")
        return tuple(changes)

    @staticmethod
    def _tokens(value: str) -> frozenset[str]:
        return frozenset(item.group(0).casefold() for item in _TOKEN.finditer(value))
