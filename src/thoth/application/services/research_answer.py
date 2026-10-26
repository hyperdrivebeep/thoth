"""Answer presentation is fixed at generation time; display locale never rewrites it."""

from thoth.application.services.request_language import partial_answer
from thoth.application.services.research_coverage import answer_assessment_state
from thoth.domain.evidence_requirements import CoverageAssessment


def answer_summary(
    coverage: CoverageAssessment,
    answer: str,
    language: str | None,
    *,
    absence_hold: bool = False,
) -> dict[str, str]:
    state = "PARTIAL_HOLD" if absence_hold else answer_assessment_state(coverage)
    return {
        "answer_status": state,
        "answer": partial_answer(answer, language)
        if state == "PARTIAL_HOLD" and not absence_hold
        else answer,
    }
