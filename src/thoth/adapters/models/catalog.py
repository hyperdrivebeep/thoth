"""Read non-secret local Codex metadata; advertise only direct-wire effort levels."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from thoth.adapters.models.catalog_startup import SingleFlight
from thoth.domain.model_catalog import CatalogProviderStatus, ExclusionReason
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model_catalog import CatalogStatusPort, ModelCatalogPort

if TYPE_CHECKING:
    from thoth.adapters.models.codex_broker import CodexAuthBroker

# The direct Responses transport does not implement product orchestration levels.
WIRE_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh"})
# ChatGPT Codex Responses rejects these local-cache slugs with HTTP 400
# "not supported when using Codex with a ChatGPT account".
CHATGPT_CODEX_UNSUPPORTED_SLUGS = frozenset(
    {
        "codex-mini-latest",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-5",
        "gpt-5-codex",
        "gpt-5-mini",
        "gpt-5.3-codex-spark",
        "gpt-5.4",
        "gpt-5.4-mini",
        "o3",
        "o4-mini",
    }
)


def classify_codex_model(model: object) -> tuple[str | None, ExclusionReason | None]:
    """The usable id, or why the list's entry is left out (None, None when it is no entry)."""

    if not isinstance(model, str) or not model:
        return None, None
    if "/" in model:
        return None, "NAMESPACED_ID"
    if model in CHATGPT_CODEX_UNSUPPORTED_SLUGS:
        return None, "UNSUPPORTED_SLUG"
    return model, None


def chatgpt_codex_model_id(model: object) -> str | None:
    return classify_codex_model(model)[0]


class StaticModelCatalog:
    def __init__(
        self,
        options: tuple[ModelOption, ...] = (),
        defaults: ModelSelection = ModelSelection(provider="default"),
    ) -> None:
        self._options, self._defaults = options, defaults

    def options(self) -> tuple[ModelOption, ...]:
        return self._options

    def defaults(self) -> ModelSelection:
        return self._defaults


class CodexModelCatalog:
    provider = "codex-oauth"

    def __init__(self, workspace: Path, *, broker: CodexAuthBroker | None = None) -> None:
        from thoth.adapters.models.codex_broker import broker_for_workspace

        self._broker = broker or broker_for_workspace(workspace)
        self._flight = SingleFlight()

    def defaults(self) -> ModelSelection:
        return self._broker.cached_state().default

    def options(self) -> tuple[ModelOption, ...]:
        return self._broker.cached_state().options

    def refresh(self) -> None:
        """Fetch the list now; a request that overlaps one in flight waits for it instead."""

        self._flight.run(self._fetch)

    def _fetch(self) -> None:
        self._broker.state(force=True)

    def refresh_due(self, now: datetime, max_age: timedelta) -> bool:
        """Signed in, and the stored list is missing or older than `max_age`."""

        if not self._broker.profile.auth_path.is_file():
            return False
        snapshot = self._broker.catalog_snapshot()
        return snapshot is None or now - snapshot.fetched_at > max_age

    def statuses(self) -> tuple[CatalogProviderStatus, ...]:
        snapshot = self._broker.catalog_snapshot()
        if snapshot is None or not self._broker.cached_state().options:
            return ()
        return (
            CatalogProviderStatus(
                provider=snapshot.provider,
                source=snapshot.source,
                status=snapshot.status,
                fetched_at=snapshot.fetched_at,
                failure_reason=snapshot.failure_reason,
                excluded=snapshot.excluded,
            ),
        )

    def record_execution(
        self, provider: str, model: str, state: str, reason_code: str | None
    ) -> None:
        if provider == "codex-oauth":
            self._broker.record_execution(model, state, reason_code)


class CompositeModelCatalog:
    def __init__(self, *catalogs: ModelCatalogPort, defaults: ModelSelection | None = None) -> None:
        self._catalogs = catalogs
        self._defaults = defaults

    def refresh(self) -> None:
        for catalog in self._catalogs:
            refresh = getattr(catalog, "refresh", None)
            if callable(refresh):
                refresh()

    def statuses(self) -> tuple[CatalogProviderStatus, ...]:
        found: list[CatalogProviderStatus] = []
        for catalog in self._catalogs:
            if isinstance(catalog, CatalogStatusPort):
                found.extend(catalog.statuses())
        return tuple(found)

    def record_execution(
        self, provider: str, model: str, state: str, reason_code: str | None
    ) -> None:
        for catalog in self._catalogs:
            record = getattr(catalog, "record_execution", None)
            if callable(record):
                record(provider, model, state, reason_code)

    def defaults(self) -> ModelSelection:
        if self._defaults is not None:
            return self._defaults
        for catalog in self._catalogs:
            defaults = catalog.defaults()
            advertised = {(option.provider, option.model) for option in catalog.options()}
            if (
                defaults.provider is not None
                and defaults.model is not None
                and (defaults.provider, defaults.model) in advertised
            ):
                return defaults
        for catalog in self._catalogs:
            if getattr(catalog, "fallback_default_allowed", True) is False:
                continue
            options = catalog.options()
            if options:
                option = options[0]
                return ModelSelection(
                    provider=option.provider,
                    model=option.model,
                    reasoning_effort=option.default_effort,
                )
        return ModelSelection()

    def options(self) -> tuple[ModelOption, ...]:
        seen: set[tuple[str, str]] = set()
        options: list[ModelOption] = []
        for catalog in self._catalogs:
            for option in catalog.options():
                key = (option.provider, option.model)
                if key in seen:
                    continue
                seen.add(key)
                options.append(option)
        return tuple(options)
