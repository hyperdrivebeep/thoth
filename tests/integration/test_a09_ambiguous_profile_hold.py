from __future__ import annotations

from pathlib import Path

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteCriterionContractStore
from thoth.application.services.criterion_profile_router import (
    SourceGroundedCriterionProfileRouter,
)
from thoth.apps.runtime import create_runtime
from thoth.domain.artifact import SourceLocator
from thoth.domain.canonical import domain_digest
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan


def profile_span(text: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="span:a09:ambiguous",
        project_id="project:a09:ambiguous",
        artifact_id="artifact:a09:ambiguous",
        source_version_id="source-version:a09:ambiguous",
        locator=SourceLocator(file_path="ambiguous.md"),
        exact_text=text,
        text_sha256=domain_digest("A09_TEST_TEXT", "1.0.0", text.encode()),
        extraction_method="TEST",
        support_state=SupportState.SUPPORTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def test_ambiguous_specialized_profile_candidates_hold_for_owner_decision(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "ambiguous-profile")
    try:
        decision = SourceGroundedCriterionProfileRouter(
            profiles=SqliteCriterionContractStore(runtime.ledger.engine),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            general_profile_ref="GENERAL_RND",
        ).route(
            project_id="project:a09:ambiguous",
            evidence=(
                profile_span(
                    "An accredited laboratory clinical trial requires ISO 17025 calibration, "
                    "a prespecified statistical endpoint, confidence interval, and protocol."
                ),
            ),
        )
        assert decision.state == "PROFILE_DECISION_REQUIRED"
        assert "ACCREDITED_LAB_CONFORMITY" in decision.candidate_profile_refs
        assert "CLINICAL_STATISTICAL_ANALYSIS" in decision.candidate_profile_refs
        assert decision.selected_profile_refs == ()
        assert decision.profile_decision_required is True
        assert decision.applicability_evidence
    finally:
        runtime.close()
