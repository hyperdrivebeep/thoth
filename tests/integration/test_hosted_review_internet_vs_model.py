from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.deployment_mode import DeploymentMode


@pytest.mark.asyncio
async def test_denied_keeps_model_path_and_blocks_web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model)
    try:
        await runtime.bus.dispatch(
            request("workspace/setup/update", "deny", {"internet_consent": "DENIED"})
        )
        ready = value(await runtime.bus.query(request("workspace/ready", "ready", {})))
        assert ready["ready"] is True
        project = value(
            await runtime.bus.query(request("project/read", "pread", {"project_id": "p"}))
        )
        denied = await runtime.bus.dispatch(
            request(
                "project/policy/update",
                "web-on",
                {
                    "project_id": "p",
                    "expected_revision": project["revision"],
                    "payload": {
                        "public_web": {"enabled": True, "preferred_hosts": ["arxiv.org"]}
                    },
                },
            )
        )
        assert denied.error is not None
        assert "PUBLIC_WEB_CONSENT_REQUIRED" in denied.error.message
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Explain LAB-42 latency.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None
        assert operation.state.value == "SUCCEEDED", operation.error
        assert model.calls
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_allowed_without_hosts_does_not_enable_web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    try:
        await runtime.bus.dispatch(
            request("workspace/setup/update", "allow", {"internet_consent": "ALLOWED"})
        )
        project = value(
            await runtime.bus.query(request("project/read", "pread", {"project_id": "p"}))
        )
        missing_hosts = await runtime.bus.dispatch(
            request(
                "project/policy/update",
                "web-empty",
                {
                    "project_id": "p",
                    "expected_revision": project["revision"],
                    "payload": {"public_web": {"enabled": True, "preferred_hosts": []}},
                },
            )
        )
        assert missing_hosts.error is not None
        assert missing_hosts.error.message == "PUBLIC_WEB_HOSTS_REQUIRED"
    finally:
        runtime.close()
