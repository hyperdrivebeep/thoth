"""Read non-secret local Codex metadata; advertise only direct-wire effort levels."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model_catalog import ModelCatalogPort

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


def chatgpt_codex_model_id(model: object) -> str | None:
    if not isinstance(model, str) or not model or "/" in model:
        return None
    if model in CHATGPT_CODEX_UNSUPPORTED_SLUGS:
        return None
    return model


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
    def __init__(self, workspace: Path, *, broker: CodexAuthBroker | None = None) -> None:
        from thoth.adapters.models.codex_broker import broker_for_workspace

        self._broker = broker or broker_for_workspace(workspace)

    def defaults(self) -> ModelSelection:
        return self._broker.cached_state().default

    def options(self) -> tuple[ModelOption, ...]:
        return self._broker.cached_state().options

    def refresh(self) -> None:
        self._broker.state(force=True)


class CompositeModelCatalog:
    def __init__(self, *catalogs: ModelCatalogPort, defaults: ModelSelection | None = None) -> None:
        self._catalogs = catalogs
        self._defaults = defaults

    def refresh(self) -> None:
        for catalog in self._catalogs:
            refresh = getattr(catalog, "refresh", None)
            if callable(refresh):
                refresh()

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
