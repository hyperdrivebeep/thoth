"""Find every member of an ambiguous prediction group; never choose a first winner."""

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from thoth.domain.base import DomainModel


class PredictionIdentityIssue(DomainModel):
    reason: Literal["DUPLICATE_TEST_ID", "DUPLICATE_HYPOTHESIS"]
    test_id: str
    test_index: int
    row_index: int | None = None
    hypothesis_id: str | None = None


def prediction_identity_issues(
    groups: Sequence[tuple[str, Sequence[str]]],
) -> tuple[PredictionIdentityIssue, ...]:
    """Input is one owner's (test id, target hypothesis ids) groups, in source order.

    A duplicate test makes its entire group ambiguous. Otherwise only repeated target
    rows are ambiguous. Callers may exclude those groups and retain unrelated content.
    """
    test_counts = Counter(test_id for test_id, _ in groups)
    issues: list[PredictionIdentityIssue] = []
    for test_index, (test_id, targets) in enumerate(groups):
        if test_counts[test_id] > 1:
            issues.append(
                PredictionIdentityIssue(
                    reason="DUPLICATE_TEST_ID", test_id=test_id, test_index=test_index
                )
            )
            continue
        counts = Counter(targets)
        for row_index, target in enumerate(targets):
            if counts[target] > 1:
                issues.append(
                    PredictionIdentityIssue(
                        reason="DUPLICATE_HYPOTHESIS",
                        test_id=test_id,
                        test_index=test_index,
                        row_index=row_index,
                        hypothesis_id=target,
                    )
                )
    return tuple(issues)
