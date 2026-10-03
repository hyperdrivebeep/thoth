"""Tell the model catalog what a real research request showed about the model it used."""

from __future__ import annotations

from contextlib import suppress

from thoth.domain.model_catalog import is_model_rejection
from thoth.domain.research_request import ThreadRequestRevision
from thoth.ports.model_catalog import ModelExecutionRecorderPort


def record_model_outcome(
    settings_service: object, request: ThreadRequestRevision, failure_reason: str | None = None
) -> None:
    """Mark the model verified (no failure) or refused (the provider refused the model or account).

    A network failure, a timeout or a quota or budget limit says nothing about the model and is
    not recorded. Nothing here chooses or changes a model. Recording never fails the research.
    """

    catalog = getattr(settings_service, "catalog", None)
    settings = request.model_settings
    if (
        settings is None
        or settings.model is None
        or not isinstance(catalog, ModelExecutionRecorderPort)
    ):
        return
    if failure_reason is None:
        state, reason = "VERIFIED", None
    elif is_model_rejection(failure_reason):
        state, reason = "REJECTED", failure_reason
    else:
        return
    with suppress(Exception):
        catalog.record_execution(settings.provider, settings.model, state, reason)
