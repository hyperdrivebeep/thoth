"""The background indexer swallows failures so that no request fails; the log still names them."""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from thoth.application.services.auto_index import AutoIndexer
from thoth.application.services.local_retrieval import LocalRetrievalService
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.model_asset import ModelAssetError

LOGGER = "thoth.application.services.auto_index"


class Store:
    def __init__(self) -> None:
        self.broken: set[str] = set()

    def resume_interrupted(self, project: str, now_ms: int) -> None:
        del now_ms
        if project in self.broken:
            raise OSError(f"cannot read the jobs of {project}")

    def needs_indexing(self, project: str) -> bool:
        return project == "p-waiting"


def _claim(task: object, project: str, auto: bool) -> None:
    del task, project, auto


def _release(task: object) -> None:
    del task


def indexer(store: Store) -> AutoIndexer:
    projects = [
        SimpleNamespace(project_id=name, lifecycle=ProjectLifecycle.ACTIVE)
        for name in ("p-broken", "p-waiting")
    ]
    service = SimpleNamespace(
        projects=SimpleNamespace(list=lambda: projects),
        clock=SimpleNamespace(now=lambda: datetime(2026, 11, 12, tzinfo=UTC)),
        store=store,
        closed=False,
        claim_index_slot=_claim,
        release_index_slot=_release,
    )
    return AutoIndexer(cast(LocalRetrievalService, service))


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def test_a_project_whose_jobs_cannot_be_read_is_skipped_and_named_once_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = Store()
    store.broken.add("p-broken")
    auto = indexer(store)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(3):  # three ticks
            assert auto.projects_needing_index() == ["p-waiting"]
    messages = _warnings(caplog)
    assert len(messages) == 1
    assert messages[0].startswith(
        "background failure at auto-index check project_id=p-broken\n"
        "OSError: cannot read the jobs of p-broken\n"
    )


async def test_an_indexing_failure_is_logged_but_missing_model_files_are_not(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    auto = indexer(Store())
    outcome: list[BaseException | None] = [RuntimeError("worker crashed")]

    async def run_project(project: str) -> None:
        del project
        if outcome[0] is not None:
            raise outcome[0]

    monkeypatch.setattr(auto, "run_project", run_project)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await auto._index_one("p-waiting")  # pyright: ignore[reportPrivateUsage]
        await auto._index_one("p-waiting")  # pyright: ignore[reportPrivateUsage]
        assert len(_warnings(caplog)) == 1  # the retry five minutes later fails the same way
        assert _warnings(caplog)[0].startswith(
            "background failure at auto-index project_id=p-waiting\nRuntimeError: worker crashed\n"
        )

        outcome[0] = None  # it works once, then the same failure comes back: that is news again
        await auto._index_one("p-waiting")  # pyright: ignore[reportPrivateUsage]
        outcome[0] = RuntimeError("worker crashed")
        await auto._index_one("p-waiting")  # pyright: ignore[reportPrivateUsage]
        assert len(_warnings(caplog)) == 2

        # Model files that are not installed are the normal state the settings card shows.
        outcome[0] = ModelAssetError("MODEL_ASSET_MISSING")
        await auto._index_one("p-waiting")  # pyright: ignore[reportPrivateUsage]
        assert len(_warnings(caplog)) == 2
