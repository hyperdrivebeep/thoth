"""Reading or saving model settings uses the stored list only; the list is fetched on request."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.apps.runtime import create_runtime
from thoth.domain.model_catalog import CatalogProviderStatus, ExcludedModel
from thoth.domain.model_settings import ModelOption

pytestmark = pytest.mark.usefixtures("xai_http_guard")
NOW = datetime(2026, 10, 1, 5, 2, tzinfo=UTC)


class CountingCatalog(StaticModelCatalog):
    def __init__(self, status: str = "ACTIVE") -> None:
        super().__init__(
            (
                ModelOption(
                    provider="codex-oauth",
                    model="gpt-test",
                    reasoning_efforts=("low", "high"),
                    default_effort="low",
                    capability_source="controlled",
                    entitlement="PROVIDER_LISTED",
                    execution="REJECTED",
                ),
            )
        )
        self.refreshes = 0
        self.status = status

    def refresh(self) -> None:
        self.refreshes += 1

    def statuses(self) -> tuple[CatalogProviderStatus, ...]:
        return (
            CatalogProviderStatus(
                provider="codex-oauth",
                source="PROVIDER_LIST",
                status="STALE_LAST_GOOD",
                fetched_at=NOW,
                failure_reason="CATALOG_UNAVAILABLE",
                excluded=(ExcludedModel(model="gpt-5.4", reason="UNSUPPORTED_SLUG"),),
            ),
        )


async def project(runtime: object) -> None:
    value(
        await runtime.bus.dispatch(  # type: ignore[attr-defined]
            request(
                "project/create",
                "cache-project",
                {
                    "project_id": "project:cache",
                    "name": "Cache",
                    "cutoff_at": "2026-09-24T00:00:00Z",
                },
            )
        )
    )


@pytest.mark.asyncio
async def test_settings_read_and_update_never_ask_the_provider_for_a_list(tmp_path: Path) -> None:
    catalog = CountingCatalog()
    runtime = create_runtime(tmp_path / "a", model_catalog=catalog)
    try:
        await project(runtime)
        for index in range(3):
            read = value(
                await runtime.bus.query(
                    request("model/settings/read", f"read-{index}", {"project_id": "project:cache"})
                )
            )
        value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "update-1",
                    {
                        "project_id": "project:cache",
                        "selection": {
                            "provider": "codex-oauth",
                            "model": "gpt-test",
                            "reasoning_effort": "high",
                        },
                    },
                )
            )
        )
        assert catalog.refreshes == 0
        (option,) = read["model_options"]
        assert option["entitlement"] == "PROVIDER_LISTED" and option["execution"] == "REJECTED"
        assert read["catalog_status"] == [
            {
                "provider": "codex-oauth",
                "source": "PROVIDER_LIST",
                "status": "STALE_LAST_GOOD",
                "fetched_at": "2026-10-01T05:02:00Z",
                "failure_reason": "CATALOG_UNAVAILABLE",
                "excluded": [{"model": "gpt-5.4", "reason": "UNSUPPORTED_SLUG"}],
            }
        ]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_selection_missing_from_the_stored_list_is_refused_with_the_list_state(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "a", model_catalog=CountingCatalog())
    try:
        await project(runtime)
        rejected = await runtime.bus.dispatch(
            request(
                "model/settings/update",
                "update-missing",
                {
                    "project_id": "project:cache",
                    "selection": {
                        "provider": "codex-oauth",
                        "model": "gone",
                        "reasoning_effort": "low",
                    },
                },
            )
        )
        assert rejected.error is not None
        assert "MODEL_CAPABILITY_UNKNOWN" in rejected.error.message
        assert rejected.error.data["catalog_status"] == "STALE_LAST_GOOD"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_explicit_refresh_fetches_once_and_returns_the_list_state(tmp_path: Path) -> None:
    catalog = CountingCatalog()
    runtime = create_runtime(tmp_path / "a", model_catalog=catalog)
    try:
        refreshed = value(
            await runtime.bus.dispatch(
                request("model/catalog/refresh", "refresh-now", {"project_id": "system:workspace"})
            )
        )
        assert catalog.refreshes == 1
        assert refreshed["catalog_status"][0]["status"] == "STALE_LAST_GOOD"
    finally:
        runtime.close()
