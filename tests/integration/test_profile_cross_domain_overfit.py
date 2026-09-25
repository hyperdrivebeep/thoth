from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_a09_ambiguous_profile_hold import profile_span

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteCriterionContractStore
from thoth.application.services.criterion_profile_router import (
    SourceGroundedCriterionProfileRouter,
)
from thoth.apps.runtime import create_runtime


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        (
            "system requirements verification inspection and acceptance procedure",
            "SYSTEMS_ENGINEERING_VERIFICATION",
        ),
        (
            "ISO 17025 accredited laboratory calibration uncertainty",
            "ACCREDITED_LAB_CONFORMITY",
        ),
        (
            "clinical trial endpoint confidence interval prespecified analysis",
            "CLINICAL_STATISTICAL_ANALYSIS",
        ),
        ("AI TEVV red team robustness evaluation dataset drift", "AI_TEVV"),
        (
            "national R&D performance indicator ministry program evaluation",
            "NATIONAL_RND_PERFORMANCE",
        ),
    ),
)
def test_specialized_profile_terms_do_not_cross_domain_overfit(
    tmp_path: Path,
    text: str,
    expected: str,
) -> None:
    runtime = create_runtime(tmp_path / expected.casefold())
    try:
        decision = SourceGroundedCriterionProfileRouter(
            profiles=SqliteCriterionContractStore(runtime.ledger.engine),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            general_profile_ref="GENERAL_RND",
        ).route(
            project_id=f"project:a09:{expected.casefold()}",
            evidence=(profile_span(text),),
        )
        assert decision.candidate_profile_refs == (expected,)
        assert decision.state == "PROFILE_DECISION_REQUIRED"
    finally:
        runtime.close()


def test_generic_research_text_stays_on_general_core(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "general")
    try:
        decision = SourceGroundedCriterionProfileRouter(
            profiles=SqliteCriterionContractStore(runtime.ledger.engine),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            general_profile_ref="GENERAL_RND",
        ).route(
            project_id="project:a09:general",
            evidence=(
                profile_span(
                    "Compare two prototypes using available evidence and document uncertainty."
                ),
            ),
        )
        assert decision.selected_profile_refs == ("GENERAL_RND",)
        assert decision.candidate_profile_refs == ()
    finally:
        runtime.close()
