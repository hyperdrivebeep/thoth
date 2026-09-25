"""Compose the registered model providers with current resource authorization."""

from pathlib import Path

from thoth.adapters.models import CodexOAuthModel, RegisteredModelResolver
from thoth.adapters.models.codex_http import CodexHttpExecutor, CodexLocalSession
from thoth.adapters.models.local_credentials import register_thoth_local_providers
from thoth.application.services.research_models import ResearchModels
from thoth.application.services.resource_scope_models import ResourceScopedModels
from thoth.apps.behavior_composition import BehaviorRuntime
from thoth.ports.model import ModelResolverPort
from thoth.ports.resource_scope import ResourceAccessPort


def create_models(
    models: ModelResolverPort | None,
    access: ResourceAccessPort,
    workspace: Path | None = None,
) -> ModelResolverPort:
    if models is None:
        defaults = RegisteredModelResolver()
        defaults.register(
            "default",
            lambda model: CodexOAuthModel(CodexHttpExecutor(CodexLocalSession(model=model))),
        )
        defaults.register(
            "codex-oauth",
            lambda model: CodexOAuthModel(CodexHttpExecutor(CodexLocalSession(model=model))),
        )
        register_thoth_local_providers(defaults, workspace)
        models = defaults
    return ResourceScopedModels(models, access)


def wrap_research_models(behavior: BehaviorRuntime, models: ModelResolverPort) -> ResearchModels:
    return ResearchModels(behavior.wrap_models(models))
