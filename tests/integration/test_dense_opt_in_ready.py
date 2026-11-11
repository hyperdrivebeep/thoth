"""When a project counts as switched on with its index ready, over the real index store."""

from pathlib import Path

import pytest
from sqlalchemy import update
from tests.embedding_fixtures import PROJECT, IndexFixture, fixture

from thoth.adapters.storage import schema
from thoth.application.services.dense_opt_in import ProjectDenseOptIn


def checker(x: IndexFixture, installed: bool = True) -> ProjectDenseOptIn:
    return ProjectDenseOptIn(
        store=x.stores.embedding_index,
        governance=x.stores.governance,
        dependencies=lambda: installed,
    )


async def index(x: IndexFixture) -> None:
    x.service.reconcile(PROJECT)
    assert all(job.state == "READY" for job in await x.service.drain(PROJECT))


async def test_a_project_counts_only_while_every_active_source_has_a_current_ready_index(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path, enabled=False)
    try:
        ready = checker(x)
        first = x.ingest("a")
        assert ready(PROJECT) is False  # switched off
        x.service.configure(
            PROJECT, True, x.stores.embedding_index.configuration(PROJECT).generation
        )
        assert ready(PROJECT) is False  # switched on, nothing indexed yet
        await index(x)
        assert ready(PROJECT) is True
        assert checker(x, installed=False)(PROJECT) is False  # the optional runtime is missing
        x.ingest("b")
        assert ready(PROJECT) is False  # a source added after indexing is not indexed
        await index(x)
        assert ready(PROJECT) is True
        with x.stores.ledger.engine.begin() as conn:  # the source changed after it was indexed
            conn.execute(
                update(schema.artifact_versions)
                .where(schema.artifact_versions.c.source_version_id == first.source_version_id)
                .values(byte_sha256="0" * 64)
            )
        assert ready(PROJECT) is False
    finally:
        x.stores.close()


async def test_the_enabled_flag_counts_even_when_every_job_still_looks_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    x = fixture(tmp_path)
    try:
        ready = checker(x)
        x.ingest("a")
        await index(x)
        assert ready(PROJECT) is True
        store = x.stores.embedding_index
        real = store.configuration
        # Same generation and model, so only the flag differs from the passing case above.

        def disabled(project: str) -> object:
            return real(project).model_copy(update={"enabled": False})

        monkeypatch.setattr(store, "configuration", disabled)
        assert ready(PROJECT) is False
    finally:
        x.stores.close()


async def test_switching_off_and_a_project_without_sources_do_not_count(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        ready = checker(x)
        assert ready(PROJECT) is False  # on, but no active source
        x.ingest("a")
        await index(x)
        assert ready(PROJECT) is True
        generation = x.stores.embedding_index.configuration(PROJECT).generation
        x.service.configure(PROJECT, False, generation)
        assert ready(PROJECT) is False
        assert ready("project:unknown") is False
    finally:
        x.stores.close()
