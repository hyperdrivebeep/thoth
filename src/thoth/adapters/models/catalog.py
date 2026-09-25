"""Read non-secret local Codex metadata; advertise only direct-wire effort levels."""

import json
import os
import tomllib
from pathlib import Path

from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model_catalog import ModelCatalogPort

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
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        cache_override = os.environ.get("THOTH_CODEX_MODELS_CACHE")
        self.cache_path = (
            Path(cache_override).expanduser() if cache_override else self.root / "models_cache.json"
        )

    def defaults(self) -> ModelSelection:
        try:
            config = tomllib.loads((self.root / "config.toml").read_text(encoding="utf-8-sig"))
            model = chatgpt_codex_model_id(config.get("model"))
            return ModelSelection(
                provider="codex-oauth",
                model=model,
                reasoning_effort=config.get("model_reasoning_effort"),
            )
        except (OSError, ValueError):
            return ModelSelection(provider="codex-oauth")

    def options(self) -> tuple[ModelOption, ...]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8-sig"))
            result: list[ModelOption] = []
            for entry in raw["models"]:
                # Aggregated local catalogs also contain routes for other providers.
                # A namespaced router alias is not a direct Codex Responses model ID.
                slug = chatgpt_codex_model_id(entry.get("slug"))
                if slug is None or entry.get("visibility", "list") != "list":
                    continue
                efforts = tuple(
                    item["effort"]
                    for item in entry["supported_reasoning_levels"]
                    if item["effort"] in WIRE_EFFORTS
                )
                result.append(
                    ModelOption(
                        provider="codex-oauth",
                        model=slug,
                        reasoning_efforts=efforts,
                        default_effort=entry.get("default_reasoning_level"),
                        capability_source="codex-local-catalog/direct-responses-v1",
                    )
                )
            return tuple(result)
        except (OSError, ValueError, KeyError, TypeError):
            return ()


class CompositeModelCatalog:
    def __init__(self, *catalogs: ModelCatalogPort, defaults: ModelSelection | None = None) -> None:
        self._catalogs = catalogs
        self._defaults = defaults

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
