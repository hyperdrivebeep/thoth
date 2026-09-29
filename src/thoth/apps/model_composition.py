"""Compose the registered model providers with current resource authorization."""

from pathlib import Path

import httpx

from thoth.adapters.models import CodexOAuthModel, RegisteredModelResolver
from thoth.adapters.models.claude_code import claude_code_model_factory
from thoth.adapters.models.claude_messages import create_claude_oauth_model
from thoth.adapters.models.codex_broker import CodexAuthBroker, broker_for_workspace
from thoth.adapters.models.codex_http import CodexHttpExecutor, CodexLocalSession
from thoth.adapters.models.local_credentials import register_thoth_local_providers
from thoth.adapters.models.xai_broker import XaiAuthBroker
from thoth.adapters.models.xai_broker import broker_for_workspace as xai_broker_for_workspace
from thoth.adapters.models.xai_model import XaiWorkspaceModel
from thoth.application.services.research_models import ResearchModels
from thoth.application.services.resource_scope_models import ResourceScopedModels
from thoth.apps.behavior_composition import BehaviorRuntime
from thoth.ports.model import ModelExecutionHold, ModelPort, ModelResolverPort
from thoth.ports.resource_scope import ResourceAccessPort


def create_codex_model(
    workspace: Path,
    model: str | None = None,
    *,
    broker: CodexAuthBroker | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelPort:
    """Bind one local workspace to the same isolated broker and no-tools HTTP transport."""
    broker = broker or broker_for_workspace(workspace)
    return CodexOAuthModel(
        CodexHttpExecutor(
            CodexLocalSession(workspace, model=model, broker=broker), transport=transport
        )
    )


def _default_codex(workspace: Path | None, model: str | None) -> ModelPort:
    if workspace is None:
        raise ModelExecutionHold("CODEX_WORKSPACE_REQUIRED")
    return create_codex_model(workspace, model)


def _claude_candidate(workspace: Path | None, model: str | None) -> ModelPort:
    if workspace is None:
        raise ModelExecutionHold("CLAUDE_CODE_WORKSPACE_REQUIRED")
    return claude_code_model_factory(workspace, model)


def _claude_oauth_candidate(workspace: Path | None, model: str | None) -> ModelPort:
    if workspace is None:
        raise ModelExecutionHold("CLAUDE_WORKSPACE_REQUIRED")
    return create_claude_oauth_model(workspace, model)


def create_xai_oauth_model(
    workspace: Path,
    model: str | None = None,
    *,
    broker: XaiAuthBroker | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelPort:
    del model  # The resolved request settings are the authority for model/effort.
    return XaiWorkspaceModel(broker or xai_broker_for_workspace(workspace), transport=transport)


def _xai_candidate(workspace: Path | None, model: str | None) -> ModelPort:
    if workspace is None:
        raise ModelExecutionHold("XAI_WORKSPACE_REQUIRED")
    return create_xai_oauth_model(workspace, model)


def create_models(
    models: ModelResolverPort | None,
    access: ResourceAccessPort,
    workspace: Path | None = None,
) -> ModelResolverPort:
    if models is None:
        defaults = RegisteredModelResolver()
        defaults.register(
            "default",
            lambda model: _default_codex(workspace, model),
        )
        defaults.register(
            "codex-oauth",
            lambda model: _default_codex(workspace, model),
        )
        defaults.register("claude-code", lambda model: _claude_candidate(workspace, model))
        defaults.register("claude-oauth", lambda model: _claude_oauth_candidate(workspace, model))
        defaults.register("xai-oauth", lambda model: _xai_candidate(workspace, model))
        register_thoth_local_providers(defaults, workspace)
        models = defaults
    return ResourceScopedModels(models, access)


def wrap_research_models(behavior: BehaviorRuntime, models: ModelResolverPort) -> ResearchModels:
    return ResearchModels(behavior.wrap_models(models))
