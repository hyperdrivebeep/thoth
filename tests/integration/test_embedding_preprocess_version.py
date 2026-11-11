"""Preprocessing version 1.1.0: old rows load and rebuild, no reinstall, cut passages recorded.

Synthetic encoders and sources only; no downloaded weights and no provider calls.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import text
from tests.embedding_fixtures import PROJECT, FakeWorker, IndexFixture, fixture, unit_vector
from tests.integration.test_local_retrieval_preparation import setup as retrieval_setup

from thoth.adapters.embeddings.assets import LocalModelAssets
from thoth.adapters.embeddings.manifest import granite_manifest
from thoth.adapters.embeddings.onnx_encoder import PASSAGE_TOKEN_CAP
from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.domain.embedding_index import (
    EmbeddingIndexConfig,
    EmbeddingIntent,
    EmbeddingVector,
    content_key,
    embedding_key,
    source_refs,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_embedding import (
    GRANITE_ASSET_PREPROCESSING,
    GRANITE_PREPROCESSING,
    GRANITE_PREPROCESSING_V1,
    DenseModelPin,
    window_count,
)

# The packaged manifest digest before this version existed. If it changes, every installed model
# folder (named by this digest) stops being found and the model would have to be downloaded again.
INSTALLED_FOLDER_DIGEST = "cf15f3d486a24e2861d4a5270948f9b4acf178520a751180c40e109a0d37b653"
OLD_PIN = DenseModelPin(preprocessing=GRANITE_PREPROCESSING_V1)


def test_the_version_names_the_cap_and_keeps_the_old_value_readable() -> None:
    match = re.search(r"-cap(\d+):", GRANITE_PREPROCESSING)
    assert match is not None and int(match.group(1)) == PASSAGE_TOKEN_CAP
    assert DenseModelPin().preprocessing == GRANITE_PREPROCESSING != GRANITE_PREPROCESSING_V1
    assert OLD_PIN.preprocessing == GRANITE_PREPROCESSING_V1
    assert embedding_key("a" * 64, OLD_PIN) != embedding_key("a" * 64, DenseModelPin())


def test_raising_the_version_does_not_ask_for_a_reinstall(tmp_path: Path) -> None:
    manifest = granite_manifest()
    assert manifest.preprocessing == GRANITE_ASSET_PREPROCESSING == GRANITE_PREPROCESSING_V1
    assert manifest.digest == INSTALLED_FOLDER_DIGEST
    assets = LocalModelAssets(tmp_path / "models")
    assert assets.destination.name == INSTALLED_FOLDER_DIGEST
    assert assets.status().manifest_digest == INSTALLED_FOLDER_DIGEST


def _old_config_json(x: IndexFixture) -> str:
    config = x.stores.embedding_index.configuration(PROJECT)
    assert config.model == DenseModelPin()
    stored = config.model_copy(update={"model": OLD_PIN}).model_dump_json()
    assert GRANITE_PREPROCESSING_V1 in stored and GRANITE_PREPROCESSING not in stored
    return stored


def _store_old_config(x: IndexFixture) -> None:
    stored = _old_config_json(x)
    generation = x.stores.embedding_index.configuration(PROJECT).generation
    with x.stores.ledger.engine.begin() as connection:
        # A workspace saved by 1.0.0 has a stored row; a project nobody configured has none yet.
        connection.execute(
            text(
                "INSERT INTO embedding_configs (project_id, generation, enabled, content_json)"
                " VALUES (:p, :g, 1, :json)"
                " ON CONFLICT(project_id) DO UPDATE SET content_json = :json"
            ),
            {"json": stored, "p": PROJECT, "g": generation},
        )


def _store_old_ready_job(x: IndexFixture, span: EvidenceSpan) -> str:
    """A job and a vector exactly as the 1.0.0 code stored them."""
    config = x.stores.embedding_index.configuration(PROJECT)
    intent = EmbeddingIntent(
        project_id=PROJECT,
        artifact_id=span.artifact_id,
        source_version_id=span.source_version_id,
        source_sha256="b" * 64,
        refs=source_refs((span,)),
        model=OLD_PIN,
        config_generation=config.generation,
    )
    vector = EmbeddingVector.from_values(unit_vector(span.exact_text))
    with x.stores.ledger.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO embedding_jobs (job_id, project_id, artifact_id, source_version_id,"
                " intent_json, state, generation, manifest_digest)"
                " VALUES (:id, :p, :a, :v, :json, 'READY', 1, :m)"
            ),
            {
                "id": intent.digest,
                "p": PROJECT,
                "a": span.artifact_id,
                "v": span.source_version_id,
                "json": intent.model_dump_json(),
                "m": "c" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO embedding_vectors (project_id, content_key, vector_sha256, data)"
                " VALUES (:p, :k, :s, :d)"
            ),
            {
                "p": PROJECT,
                "k": embedding_key(span.text_sha256, OLD_PIN),
                "s": vector.sha256,
                "d": vector.data,
            },
        )
    return intent.digest


def _forget_staged_job(x: IndexFixture, job_id: str) -> None:
    """A workspace saved by the 1.0.0 code has no job yet for the new version."""
    with x.stores.ledger.engine.begin() as connection:
        connection.execute(text("DELETE FROM embedding_jobs WHERE job_id = :id"), {"id": job_id})


def _job_states(x: IndexFixture) -> dict[str, str]:
    return {job.job_id: job.state for job in x.stores.embedding_index.jobs(PROJECT)}


async def test_old_rows_load_and_follow_the_usual_stale_path_to_a_rebuild(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        span = x.ingest("a")
        (new_job,) = x.stores.embedding_index.jobs(PROJECT)
        _forget_staged_job(x, new_job.job_id)
        _store_old_config(x)
        old_id = _store_old_ready_job(x, span)

        # Reading never fails and never rewrites: the saved row still names 1.0.0.
        config = x.stores.embedding_index.configuration(PROJECT)
        assert config.enabled and config.model == DenseModelPin()
        with x.stores.ledger.engine.connect() as connection:
            saved = connection.execute(
                text("SELECT content_json FROM embedding_configs")
            ).scalar_one()
        assert GRANITE_PREPROCESSING_V1 in str(saved)
        assert old_id in _job_states(x)
        # An index made the old way is not accepted as current.
        assert (
            x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
            is None
        )

        staged = x.service.reconcile(PROJECT)
        states = _job_states(x)
        assert states[old_id] == "STALE"
        assert len(staged) == 1 and staged[0] != old_id and states[staged[0]] == "PENDING"
        (ready,) = await x.service.drain(PROJECT)
        assert ready.state == "READY" and ready.intent.model == DenseModelPin()
        assert len(x.worker.calls) == 1, "the passage is encoded again, nothing is reused"
        # Old vectors are left in place; the new one has a different key.
        with x.stores.ledger.engine.connect() as connection:
            keys = {
                r[0] for r in connection.execute(text("SELECT content_key FROM embedding_vectors"))
            }
        assert {
            embedding_key(span.text_sha256, OLD_PIN),
            embedding_key(span.text_sha256, DenseModelPin()),
        } <= keys
    finally:
        x.worker.close()
        x.stores.close()


async def test_connecting_a_source_to_a_project_saved_under_1_0_0_works(tmp_path: Path) -> None:
    x = fixture(tmp_path)
    try:
        _store_old_config(x)
        span = x.ingest("later")  # persist_ingestion reads the saved configuration
        (job,) = x.stores.embedding_index.jobs(PROJECT)
        assert job.state == "PENDING" and job.intent.model == DenseModelPin()
        assert job.intent.refs[0].span_id == span.span_id
    finally:
        x.worker.close()
        x.stores.close()


async def test_the_screen_is_told_while_only_older_indexes_exist(tmp_path: Path) -> None:
    x, service = await retrieval_setup(tmp_path)
    try:
        x.service.configure(
            PROJECT, True, x.stores.embedding_index.configuration(PROJECT).generation
        )
        span = x.ingest("a")
        (new_job,) = x.stores.embedding_index.jobs(PROJECT)
        _forget_staged_job(x, new_job.job_id)
        assert service.read_project(PROJECT)["outdated_index"] is False
        _store_old_config(x)
        _store_old_ready_job(x, span)
        assert service.read_project(PROJECT)["outdated_index"] is True
        x.service.reconcile(PROJECT)
        await x.service.drain(PROJECT)
        after = service.read_project(PROJECT)
        assert after["outdated_index"] is False and after["state"] == "READY"
    finally:
        service.close()
        x.stores.close()


class _ReportingWorker(FakeWorker):
    """Says per text whether the passage was cut; None means the worker does not say."""

    def __init__(self, report: dict[str, tuple[bool | None, int | None]]) -> None:
        super().__init__()
        self.report = report

    async def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], window: int = 0
    ) -> EmbeddingVector:
        self.calls.append(text)
        truncated, tokens = self.report.get(text, (None, None))
        return EmbeddingVector(
            EmbeddingVector.from_values(
                unit_vector(text if window == 0 else f"{text}#{window}")
            ).data,
            truncated=truncated,
            source_tokens=tokens,
        )


def _recorded(x: IndexFixture, span: EvidenceSpan) -> tuple[object, object]:
    with x.stores.ledger.engine.connect() as connection:
        row = connection.execute(
            text("SELECT truncated, source_tokens FROM embedding_vectors WHERE content_key = :k"),
            {"k": embedding_key(span.text_sha256, DenseModelPin())},
        ).one()
    return row[0], row[1]


async def test_only_cut_passages_are_recorded_and_a_cached_record_is_never_overwritten(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path)
    try:
        cut, fits, unknown, cached = (
            x.ingest(name) for name in ("cut", "fits", "unknown", "cached")
        )
        x.service.worker = x.worker = _ReportingWorker(
            {
                cut.exact_text: (True, 777),
                fits.exact_text: (False, None),
                cached.exact_text: (False, None),
            }
        )
        # This passage already has stored vectors whose record says it was cut. 999 tokens need
        # three windows, so every window is stored.
        with x.stores.ledger.engine.begin() as connection:
            vector = EmbeddingVector.from_values(unit_vector(cached.exact_text))
            for window in range(window_count(999)):
                connection.execute(
                    text(
                        "INSERT INTO embedding_vectors (project_id, content_key, vector_sha256,"
                        " data, truncated, source_tokens) VALUES (:p, :k, :s, :d, 1, 999)"
                    ),
                    {
                        "p": PROJECT,
                        "k": content_key(cached.text_sha256, DenseModelPin(), window),
                        "s": vector.sha256,
                        "d": vector.data,
                    },
                )
        await x.service.drain(PROJECT)
        assert _recorded(x, cut) == (1, 777)
        assert _recorded(x, fits) == (0, None)
        assert _recorded(x, unknown) == (None, None)
        assert _recorded(x, cached) == (1, 999)
        assert cached.exact_text not in x.worker.calls
        read = x.stores.embedding_index.cached(PROJECT, cut.text_sha256)
        assert read is not None and read.truncated is True and read.source_tokens == 777
        old = x.stores.embedding_index.cached(PROJECT, unknown.text_sha256)
        assert old is not None and old.truncated is None
    finally:
        x.worker.close()
        x.stores.close()


async def test_the_worker_carries_the_cut_flag_and_size_and_leaves_short_inputs_unchanged() -> None:
    worker = ProcessEmbeddingWorker("tests.embedding_child_encoders:ReportingEncoder")
    try:
        long = await worker.encode_one("tokens:3000", role="PASSAGE")
        assert long.truncated is True and long.source_tokens == 3000
        # A stand-in worker (no model descriptor) does not say anything about fitting passages.
        for count in range(1, 129):
            text_value = f"tokens:{count}"
            short = await worker.encode_one(text_value, role="PASSAGE")
            assert short.data == EmbeddingVector.from_values(unit_vector(text_value)).data
            assert short.truncated is None and short.source_tokens is None
        assert worker.truncated_passages == 1
    finally:
        worker.close()


def test_a_blank_record_is_not_a_zero() -> None:
    with pytest.raises(Exception, match="EMBEDDING_SOURCE_TOKENS_INVALID"):
        EmbeddingVector.from_values(unit_vector("x")).__class__(
            EmbeddingVector.from_values(unit_vector("x")).data, True, 0
        )
    assert EmbeddingIndexConfig(project_id="p", actor_ref="a").model == DenseModelPin()
