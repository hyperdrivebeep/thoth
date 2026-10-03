"""Execution marks for providers whose list is fixed or read from a local tool.

Codex keeps its marks inside its own refreshed list. Claude Code and the curated providers have no
list to fetch, so this wrapper keeps the same kind of snapshot for them, only to remember what
real requests showed about each model: a success, or a refusal of the model or account.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.models.catalog_store import CatalogSnapshotStore, CatalogStoreError
from thoth.domain.model_catalog import (
    CatalogProviderStatus,
    CatalogSnapshot,
    CatalogSource,
    ExecutionMark,
    authority_digest,
)
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model_catalog import ModelCatalogPort


class MarkedCatalog:
    def __init__(
        self,
        inner: ModelCatalogPort,
        *,
        provider: str,
        source: CatalogSource,
        workspace: Path,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._inner, self._provider = inner, provider
        self._source: CatalogSource = source
        self._store = CatalogSnapshotStore(workspace)
        self._authority = authority_digest(provider, str(workspace.resolve()))
        self._now = now

    @property
    def fallback_default_allowed(self) -> bool:
        return getattr(self._inner, "fallback_default_allowed", True) is not False

    def defaults(self) -> ModelSelection:
        return self._inner.defaults()

    def refresh(self) -> None:
        refresh = getattr(self._inner, "refresh", None)
        if callable(refresh):
            refresh()

    def _saved(self) -> CatalogSnapshot | None:
        return self._store.load(self._provider, self._authority)

    def options(self) -> tuple[ModelOption, ...]:
        options = self._inner.options()
        saved = self._saved() if options else None
        marks = {} if saved is None else {mark.model: mark.state for mark in saved.executions}
        return tuple(
            option.model_copy(update={"execution": marks[option.model]})
            if option.model in marks
            else option
            for option in options
        )

    def statuses(self) -> tuple[CatalogProviderStatus, ...]:
        if not self._inner.options():
            return ()
        saved = self._saved()
        return (
            CatalogProviderStatus(
                provider=self._provider,
                source=self._source,
                status="ACTIVE",
                fetched_at=None if saved is None else saved.fetched_at,
            ),
        )

    def record_execution(
        self, provider: str, model: str, state: str, reason_code: str | None
    ) -> None:
        options = self._inner.options()
        if provider != self._provider or state not in {"VERIFIED", "REJECTED"}:
            return
        if model not in {option.model for option in options}:
            return
        now = self._now()
        saved = self._saved()
        base = saved or CatalogSnapshot(
            provider=self._provider,
            source=self._source,
            authority_digest=self._authority,
            fetched_at=now,
            validated_at=now,
            options=options,
        )
        marks = tuple(mark for mark in base.executions if mark.model != model)
        mark = ExecutionMark(
            model=model,
            state="VERIFIED" if state == "VERIFIED" else "REJECTED",
            reason_code=reason_code,
            observed_at=now,
        )
        try:
            self._store.save(
                base.model_copy(update={"options": options, "executions": (*marks, mark)})
            )
        except CatalogStoreError:
            return  # a mark is a hint; losing one changes nothing that runs
