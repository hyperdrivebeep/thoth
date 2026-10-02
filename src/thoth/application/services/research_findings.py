"""Adopt the reviewer's confirmed findings only when every cited span was supplied to it."""

from __future__ import annotations

from collections.abc import Iterable

from thoth.domain.evidence_requirements import ConfirmedFinding

MAX_CONFIRMED_FINDINGS = 12
MAX_STATEMENT_CHARS = 800


def adopt_confirmed_findings(
    findings: Iterable[ConfirmedFinding], supplied_span_ids: set[str]
) -> tuple[list[dict[str, object]], int]:
    """Return the adopted findings as stored result entries and how many were dropped.

    A finding is dropped when its statement is blank or too long, it cites no span, any cited span
    was not supplied, or the cap is already reached. Nothing is repaired or reinterpreted.
    """
    adopted: list[dict[str, object]] = []
    dropped = 0
    for item in findings:
        statement = item.statement.strip()
        refs = item.evidence_refs
        valid = (
            0 < len(statement) <= MAX_STATEMENT_CHARS
            and bool(refs)
            and all(ref in supplied_span_ids for ref in refs)
            and len(adopted) < MAX_CONFIRMED_FINDINGS
        )
        if not valid:
            dropped += 1
            continue
        adopted.append(
            {
                "statement": statement,
                "evidence_refs": list(refs),
                "requirement_id": item.requirement_id,
                "kind": item.kind,
            }
        )
    return adopted, dropped
