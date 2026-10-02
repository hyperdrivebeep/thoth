"""Which ledger heads a research attempt's save must still find unchanged.

A save used to compare every head of the project, so any unrelated write (another thread, a
user's memory correction) branched an investigation that had not read it. With a research
attempt on record, only what the attempt read or writes is compared.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from thoth.domain.research_execution import ResearchWork


@dataclass(frozen=True)
class ReadScope:
    expected_heads: dict[str, str]
    expected_absent: tuple[str, ...]


def read_scope(
    work: ResearchWork | None,
    project_id: str,
    heads: Mapping[str, str],
    written_keys: Iterable[str],
) -> ReadScope | None:
    """Heads to compare when saving, or None when nothing says what was read (compare all).

    Expected values come from the heads seen when the cycle started, not from the digests the
    attempt captured: the attempt writes some records itself before that point.
    """

    if work is None or work.request_ref.project_id != project_id:
        return None
    written = tuple(written_keys)
    request = work.request_ref
    keys = {*work.consumed_heads, *written, f"{request.entity_type}:{request.entity_id}"}
    return ReadScope(
        expected_heads={key: heads[key] for key in sorted(keys) if key in heads},
        expected_absent=tuple(sorted(key for key in written if key not in heads)),
    )
