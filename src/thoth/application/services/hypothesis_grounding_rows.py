"""Per-row grounding acceptance; technical checks never assert semantic support."""

from pydantic import ValidationError

from thoth.application.services.english_prose import EnglishProseCheck
from thoth.application.services.hypothesis_contract_v3 import language_problem
from thoth.domain.hypothesis_v5 import ExpectedResultV5
from thoth.domain.hypothesis_v5_wire import ReceivedExpectedResultV5, ReceivedFilledRowV5
from thoth.domain.model import ContextPack
from thoth.domain.prediction_grounding import GroundingRowDiagnostic
from thoth.domain.response_language import ResponseLanguage

ReceivedRow = ReceivedExpectedResultV5 | ReceivedFilledRowV5


def row_diagnostic(
    owner_id: str, test_id: str, row: ReceivedRow, reason: str
) -> GroundingRowDiagnostic:
    return GroundingRowDiagnostic(
        owner_hypothesis_id=owner_id,
        test_id=test_id,
        hypothesis_id=row.hypothesis_id,
        reason=reason,
        original_label_chars=len(row.expected or ""),
        original_basis_count=len(row.basis),
    )


def accept_grounding_row(
    row: ReceivedRow,
    known_hypotheses: set[str],
    context: ContextPack,
    language: ResponseLanguage | None,
    *,
    normalize_label: bool = False,
) -> tuple[ExpectedResultV5 | None, str | None]:
    if row.hypothesis_id not in known_hypotheses:
        return None, "UNKNOWN_HYPOTHESIS"
    if len(row.basis) > 5:
        return None, "GROUNDING_BASIS_LIMIT"
    if not set(row.basis) <= {span.span_id for span in context.evidence}:
        return None, "UNKNOWN_SPAN"
    label = row.expected
    if normalize_label and label is not None:
        label = " ".join(label.split())
    if label is not None and len(label) > 80:
        return None, "EXPECTED_TOO_LONG"
    try:
        accepted = ExpectedResultV5.model_validate(
            {
                "hypothesis_id": row.hypothesis_id,
                "knowledge_state": row.knowledge_state,
                "expected": label,
                "basis": row.basis,
                "grounding": row.grounding.model_dump(mode="python"),
            }
        )
    except ValidationError as error:
        first = error.errors(include_input=False)[0]
        cause = first.get("ctx", {}).get("error")
        reason = str(cause) if isinstance(cause, ValueError) else "GROUNDING_INVALID_ROW"
        return None, reason
    rationale = accepted.grounding.rationale
    if rationale.strip():
        if language == "en":
            checker = EnglishProseCheck.build(
                context.problem, context.evidence, tuple(known_hypotheses), context
            )
            bad = checker.mismatch(rationale)
        else:
            problem = "한국어 " + context.problem if language == "ko" else context.problem
            bad = language_problem(rationale, problem) is not None
        if bad:
            return None, "GROUNDING_RATIONALE_LANGUAGE_MISMATCH"
    return accepted, None
