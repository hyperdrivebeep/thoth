"""The verdict link of a hypothesis made while an investigation from a trace row is running."""

from __future__ import annotations

from typing import cast

from pydantic import ValidationError

from thoth.domain.research_execution import research_work
from thoth.domain.verdict_link import VerdictLink

LINK_KEYS = ("subject_kind", "subject_id", "verdict_revision", "verdict_digest", "state")


def running_verdict_link() -> VerdictLink | None:
    """The link for hypotheses staged now, or None when this run did not start from a trace row.

    The origin in the running work was written by the server from the stored trace (see
    trace_origin); a run without it, or an older origin without the verdict's content digest,
    makes no link, and the hypothesis simply has none.
    """
    work = research_work.get()
    found = None if work is None else work.context.get("origin")
    if not isinstance(found, dict):
        return None
    origin = cast(dict[str, object], found)
    if not origin.get("verdict_digest"):
        return None
    try:
        return VerdictLink.model_validate({key: origin.get(key) for key in LINK_KEYS})
    except ValidationError:
        return None
