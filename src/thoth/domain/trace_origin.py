"""Where a research request started: one verdict row of the project's verification trace.

The client names the row (kind, project, subject, the verdict revision it saw). The server
checks that name against the stored trace and writes the facts itself (state, reasons, rule,
results, source positions), so what the model and the result carry is never text the client made up.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel

ORIGIN_SCHEMA_VERSION = "1.0.0"
MAX_ORIGIN_REFS = 20
# Other conditions of the same measure under the same requirement, shown beside the row so a cause
# can be looked for in what differs. Few, with a few source positions each, to keep the context
# small.
MAX_SIBLING_VERDICTS = 4
MAX_SIBLING_SPAN_REFS = 3
MAX_COMPARISON_REFS = 8


class TraceOriginInput(DomainModel):
    """What the client sends: only the name of the row and the revision it was looking at."""

    kind: Literal["TRACE_VERDICT"]
    project_id: str = Field(min_length=1, max_length=160)
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    verdict_revision: str = Field(min_length=64, max_length=64)


class TraceSiblingVerdict(DomainModel):
    """The verdict of another condition of the same measure, as the stored trace has it."""

    criterion_id: str = Field(min_length=1, max_length=500)
    condition: str | None = Field(default=None, max_length=500)
    state: str = Field(min_length=1, max_length=100)
    rule_summary: str | None = Field(default=None, max_length=500)
    # The chosen result the verdict was computed from (none while no result is chosen).
    result_id: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=100)
    numerator: int | None = None
    denominator: int | None = None
    unit: str | None = Field(default=None, max_length=100)


class TraceVerdictOrigin(DomainModel):
    """The row as the stored trace has it, written by the server."""

    kind: Literal["TRACE_VERDICT"] = "TRACE_VERDICT"
    schema_version: Literal["1.0.0"] = ORIGIN_SCHEMA_VERSION
    project_id: str = Field(min_length=1, max_length=160)
    trace_set_digest: str = Field(min_length=1, max_length=200)
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    subject_title: str = Field(default="", max_length=500)
    verdict_revision: str = Field(min_length=64, max_length=64)
    # What the verdict says, not who confirmed it or when it was recomputed (the hypothesis link).
    verdict_digest: str | None = Field(default=None, max_length=200)
    state: str = Field(min_length=1, max_length=100)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=MAX_ORIGIN_REFS)
    condition: str | None = Field(default=None, max_length=500)
    rule_summary: str | None = Field(default=None, max_length=500)
    chosen_result_ids: tuple[str, ...] = Field(default=(), max_length=MAX_ORIGIN_REFS)
    source_span_refs: tuple[str, ...] = Field(default=(), max_length=MAX_ORIGIN_REFS)
    sibling_verdicts: tuple[TraceSiblingVerdict, ...] = Field(
        default=(), max_length=MAX_SIBLING_VERDICTS
    )
    # Where the siblings' own results are written; pinned beside source_span_refs.
    comparison_span_refs: tuple[str, ...] = Field(default=(), max_length=MAX_COMPARISON_REFS)
