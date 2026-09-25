from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.apps.hosted_review_composition import (
    hosted_active_project_count,
    hosted_openai_resolver,
    hosted_review_catalog,
    hosted_review_contract,
)
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.project import Project
from thoth.ports.model import ModelExecutionHold, ModelResolutionError
from thoth.ports.project import ProjectStorePort


def test_hosted_catalog_is_openai_gpt55_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOSTED_REVIEW_MODEL", raising=False)
    monkeypatch.delenv("HOSTED_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    catalog = hosted_review_catalog()
    options = catalog.options()
    assert len(options) == 1
    assert options[0].provider == "openai"
    assert options[0].model == "gpt-5.5"
    defaults = catalog.defaults()
    assert defaults.provider == "openai"
    assert defaults.model == "gpt-5.5"
    resolver = hosted_openai_resolver()
    assert resolver.contains("openai")
    assert resolver.contains("default")
    assert not resolver.contains("codex-oauth")
    assert not resolver.contains("xai")
    with pytest.raises(ModelResolutionError, match="not registered"):
        resolver.resolve(provider="codex-oauth", model="gpt-5")
    with pytest.raises(ModelResolutionError, match="MODEL_ROUTE_LOCKED"):
        resolver.resolve(provider="openai", model="gpt-5")
    with pytest.raises(ModelExecutionHold, match="HOSTED_REVIEW_MODEL_UNAVAILABLE"):
        resolver.resolve(provider="openai", model="gpt-5.5")


def test_hosted_contract_rejects_unlocked_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSTED_REVIEW_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="HOSTED_REVIEW_PROVIDER_UNSUPPORTED"):
        hosted_review_contract()
    monkeypatch.setenv("HOSTED_REVIEW_PROVIDER", "openai")
    monkeypatch.setenv("HOSTED_REVIEW_MODEL", "gpt-4.1")
    with pytest.raises(RuntimeError, match="HOSTED_REVIEW_MODEL_UNSUPPORTED"):
        hosted_review_contract()
    monkeypatch.setenv("HOSTED_REVIEW_MODEL", "gpt-5")
    with pytest.raises(RuntimeError, match="HOSTED_REVIEW_MODEL_UNSUPPORTED"):
        hosted_review_contract()
    monkeypatch.setenv("HOSTED_REVIEW_MODEL", "gpt-5.5")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    with pytest.raises(RuntimeError, match="HOSTED_REVIEW_BASE_URL_LOCKED"):
        hosted_review_contract()


def test_hosted_project_quota_ignores_archived_projects() -> None:
    class Store(ProjectStorePort):
        def create(self, project: Project, *, created_at: str) -> None:
            raise AssertionError("unexpected project creation")

        def read(self, project_id: str) -> Project | None:
            raise AssertionError("unexpected project read")

        def list(self) -> tuple[Project, ...]:
            return tuple(
                Project(
                    project_id=f"project:{lifecycle.value.lower()}",
                    name=lifecycle.value,
                    cutoff_at=datetime(2026, 9, 24, tzinfo=UTC),
                    lifecycle=lifecycle,
                    overlay="fixture",
                    policy_binding_ref="policy:fixture",
                )
                for lifecycle in (ProjectLifecycle.ARCHIVED_READ_ONLY, ProjectLifecycle.DRAFT)
            )

        def update(self, project: Project, *, expected_revision: int) -> bool:
            raise AssertionError("unexpected project update")

    assert hosted_active_project_count(Store()) == 1


def test_local_mode_does_not_force_hosted_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("THOTH_DEPLOYMENT_MODE", raising=False)
    from thoth.apps.runtime import create_runtime

    runtime = create_runtime(tmp_path)
    try:
        assert os.environ.get("THOTH_DEPLOYMENT_MODE", "") != "HOSTED_REVIEW"
    finally:
        runtime.close()
