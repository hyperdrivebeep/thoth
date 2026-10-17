"""Freeze the persisted Artifact/Evidence row contract before extracting its codecs."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import orjson
import pytest
from sqlalchemy import literal, select
from sqlalchemy.engine import RowMapping

from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    StructuralNodeKind,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.project import Project

STAMP = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
ARTIFACT_CODEC = cast(
    Callable[[RowMapping], ArtifactEnvelope],
    vars(SqliteArtifactLedger)["_artifact_from_row"].__func__,
)
EVIDENCE_CODEC = cast(
    Callable[[RowMapping], EvidenceSpan], vars(SqliteArtifactLedger)["_evidence_from_row"].__func__
)


def document(identifier: str, version: str | None) -> tuple[StructuralDocument, EvidenceSpan]:
    text = f"측정 원문 {identifier}: 0.80 / 12 ms"
    digest = hashlib.sha256(text.encode()).hexdigest()
    locator = SourceLocator(line=3, file_path=f"{identifier}.txt")
    artifact = ArtifactEnvelope(
        artifact_id=f"artifact:{identifier}",
        project_id="project:codec",
        source_uri=f"fixture/{identifier}.txt",
        media_type="text/plain",
        byte_sha256=digest,
        version_label=version,
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=STAMP,
        parser_name="fixture",
        parser_version="1.0",
    )
    doc = StructuralDocument(
        artifact=artifact,
        nodes=(
            StructuralNode(
                node_id=f"node:{identifier}",
                artifact_id=artifact.artifact_id,
                parent_id=None,
                kind=StructuralNodeKind.PARAGRAPH,
                ordinal=0,
                text=text,
                locator=locator,
            ),
        ),
        extraction_coverage="FULL",
    )
    span = EvidenceSpan(
        span_id=f"span:{identifier}",
        project_id=artifact.project_id,
        artifact_id=artifact.artifact_id,
        source_version_id=f"source:{identifier}",
        locator=locator,
        exact_text=text,
        text_sha256=digest,
        extraction_method="fixture",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.SCHEMA_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )
    return doc, span


@pytest.mark.parametrize("version", (None, "release-\u03b1"))
def test_persisted_rows_keep_types_order_missing_results_and_reopen(
    tmp_path: Path, version: str | None
) -> None:
    bundle = SqliteStoreFactory().open(tmp_path)
    docs = [document(name, version) for name in ("z", "a")]
    try:
        bundle.projects.create(
            Project(
                project_id="project:codec",
                name="Codec",
                cutoff_at=STAMP,
                overlay="general-rnd",
                policy_binding_ref="policy:default",
            ),
            created_at=STAMP.isoformat(),
        )
        store = SqliteArtifactLedger(bundle.ledger.engine)
        assert store.list_artifacts("project:codec") == ()
        assert store.list_evidence("project:codec") == ()
        assert store.read_artifact("missing") is None
        assert store.read_evidence("missing") is None
        for doc, span in docs:
            store.persist_ingestion(doc, span.source_version_id, (span,))
            assert store.read_artifact(doc.artifact.artifact_id) == doc.artifact
            assert store.read_evidence(span.span_id) == span
            assert (
                store.read_structure(
                    "project:codec", doc.artifact.artifact_id, span.source_version_id
                )
                == doc
            )
        assert store.list_artifacts("project:codec") == tuple(d.artifact for d, _ in reversed(docs))
        assert store.list_evidence("project:codec") == tuple(s for _, s in reversed(docs))
        assert store.list_artifacts("another-project") == ()
        assert store.list_evidence("another-project") == ()
    finally:
        bundle.close()
    reopened = SqliteStoreFactory().open(tmp_path)
    try:
        store = SqliteArtifactLedger(reopened.ledger.engine)
        for doc, span in docs:
            assert (
                store.read_structure(
                    "project:codec", doc.artifact.artifact_id, span.source_version_id
                )
                == doc
            )
            actual = store.read_evidence(span.span_id)
            assert actual == span and actual is not None
            assert actual.support_state is SupportState.EXTRACTED
            assert actual.locator.line == 3 and "측정 원문" in actual.exact_text
    finally:
        reopened.close()


def artifact_values() -> dict[str, object]:
    return {
        "artifact_id": "artifact:x",
        "project_id": "project:codec",
        "source_uri": "fixture/x",
        "media_type": "text/plain",
        "byte_sha256": "a" * 64,
        "version_label": None,
        "authority": "OFFICIAL",
        "cutoff_state": "ELIGIBLE",
        "security_class": "INTERNAL",
        "retrieved_at": STAMP.isoformat(),
        "parser_name": "fixture",
        "parser_version": "1.0",
    }


@pytest.mark.parametrize("version", (None, "", 17))
def test_artifact_codec_preserves_null_empty_and_string_coercion(version: object) -> None:
    values = artifact_values()
    values["version_label"] = version
    # The original contract accepts mapping reads; no I/O occurs in the conversion itself.
    row = cast(RowMapping, cast(object, values))
    result = ARTIFACT_CODEC(row)
    assert result.version_label == (None if version is None else str(version))
    assert result.retrieved_at == STAMP and result.authority is AuthorityState.OFFICIAL


@pytest.mark.parametrize("field", ("authority", "retrieved_at", "source_uri"))
def test_artifact_codec_keeps_invalid_row_errors(field: str) -> None:
    values = artifact_values()
    if field == "source_uri":
        del values[field]
    else:
        values[field] = "invalid"
    row = cast(RowMapping, cast(object, values))
    with pytest.raises(KeyError if field == "source_uri" else ValueError):
        ARTIFACT_CODEC(row)


@pytest.mark.parametrize("bad_locator", (False, True))
def test_evidence_codec_consumes_sql_row_mapping_and_keeps_locator_errors(
    tmp_path: Path, bad_locator: bool
) -> None:
    _, span = document("codec", None)
    values = span.model_dump(mode="json", exclude={"locator", "schema_version"})
    values["locator_json"] = (
        "{" if bad_locator else orjson.dumps(span.locator.model_dump()).decode()
    )
    bundle = SqliteStoreFactory().open(tmp_path)
    try:
        with bundle.ledger.engine.connect() as connection:
            row = (
                connection.execute(select(*(literal(v).label(k) for k, v in values.items())))
                .mappings()
                .one()
            )
        if bad_locator:
            with pytest.raises(orjson.JSONDecodeError):
                EVIDENCE_CODEC(row)
        else:
            actual = EVIDENCE_CODEC(row)
            assert actual == span
            assert actual.verification_state is VerificationState.SCHEMA_VALID
    finally:
        bundle.close()
