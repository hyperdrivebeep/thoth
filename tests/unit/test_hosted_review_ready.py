from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.apps.hosted_review_composition import hosted_openai_resolver
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.ports.model import ModelExecutionHold, ModelResolutionError


@pytest.fixture
def hosted_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_consent_alone_makes_ready(hosted_env: Path) -> None:
    runtime = create_runtime(hosted_env, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    try:
        pending = value(await runtime.bus.query(request("workspace/ready", "ready-0", {})))
        assert pending["ready"] is False
        assert pending["deployment_mode"] == "HOSTED_REVIEW"
        assert pending["model_connected"] is False
        assert "OpenAI" in str(pending["disclosure"])
        await runtime.bus.dispatch(
            request("workspace/setup/update", "allow", {"internet_consent": "ALLOWED"})
        )
        allowed = value(await runtime.bus.query(request("workspace/ready", "ready-1", {})))
        assert allowed["ready"] is True
        assert allowed["model_connected"] is False
        assert allowed["setup"]["internet_consent"] == "ALLOWED"
        listed = value(await runtime.bus.query(request("workspace/setup/read", "read-1", {})))
        assert listed["deployment_mode"] == "HOSTED_REVIEW"
        assert listed["disclosure"] == allowed["disclosure"]
        assert listed["hosted_model"] == {"provider": "openai", "model": "gpt-5.5"}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_denied_is_ready_and_resolver_stays_registered(hosted_env: Path) -> None:
    runtime = create_runtime(hosted_env, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    try:
        await runtime.bus.dispatch(
            request("workspace/setup/update", "deny", {"internet_consent": "DENIED"})
        )
        denied = value(await runtime.bus.query(request("workspace/ready", "ready-deny", {})))
        assert denied["ready"] is True
        assert denied["setup"]["internet_consent"] == "DENIED"
        resolver = hosted_openai_resolver()
        assert resolver.contains("openai")
        with pytest.raises(ModelResolutionError, match="not registered"):
            resolver.resolve(provider="xai", model="grok-4.6")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_operator_key_marks_model_connected_without_oauth(
    hosted_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-for-network")
    runtime = create_runtime(hosted_env, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    try:
        await runtime.bus.dispatch(
            request("workspace/setup/update", "allow", {"internet_consent": "ALLOWED"})
        )
        ready = value(await runtime.bus.query(request("workspace/ready", "ready-key", {})))
        assert ready["ready"] is True
        assert ready["model_connected"] is True
        assert ready["hosted_model"] == {"provider": "openai", "model": "gpt-5.5"}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_container_without_shared_provider_budget_is_not_model_connected(
    hosted_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-for-network")
    monkeypatch.setenv("HOSTED_REVIEW_DISPATCH_GATE", "1")
    runtime = create_runtime(hosted_env, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    try:
        await runtime.bus.dispatch(
            request("workspace/setup/update", "allow", {"internet_consent": "ALLOWED"})
        )
        ready = value(await runtime.bus.query(request("workspace/ready", "ready-container", {})))
        assert ready["ready"] is False
        assert ready["model_connected"] is False
        with pytest.raises(ModelExecutionHold, match="HOSTED_REVIEW_PROVIDER_BUDGET_UNAVAILABLE"):
            hosted_openai_resolver().resolve(provider="openai", model="gpt-5.5")
    finally:
        runtime.close()
