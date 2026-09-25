from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.adapters.storage import (
    ContentAddressedObjectStore,
    SqliteArtifactLedger,
    SqliteLedger,
    SqliteProjectStore,
    migrate_sqlite_database,
)
from thoth.application.services import IngestArtifactCommand, IngestionService
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass, SupportState
from thoth.domain.project import Project


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 6, 45, tzinfo=UTC)


class SequenceIds:
    def __init__(self) -> None:
        self._next = 0

    def new(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}:{self._next}"


def test_ingestion_persists_object_structure_locator_and_candidate_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    migrate_sqlite_database(workspace / "db" / "thoth.sqlite3")
    ledger = SqliteLedger(workspace / "db" / "thoth.sqlite3")
    ledger.initialize()
    projects = SqliteProjectStore(ledger.engine)
    projects.create(
        Project(
            project_id="project:ingest",
            name="인제스트 검증",
            cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
            overlay="general-rnd",
            policy_binding_ref="policy:default",
        ),
        created_at="2026-08-30T06:45:00+00:00",
    )
    artifact_ledger = SqliteArtifactLedger(ledger.engine)
    object_store = ContentAddressedObjectStore(workspace)
    service = IngestionService(
        projects=projects,
        objects=object_store,
        parsers=default_parser_registry(),
        artifacts=artifact_ledger,
        clock=FixedClock(),
        ids=SequenceIds(),
    )
    raw = "# 시험 계획\n\n목표 정확도는 94% 이상이다.\n".encode()

    result = service.ingest(
        IngestArtifactCommand(
            project_id="project:ingest",
            source_uri="fixture/plan.md",
            media_type="text/markdown",
            raw=raw,
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            operation_id="operation:ingest",
            source_path=Path("plan.md"),
        )
    )

    assert object_store.read(result.object_digest) == raw
    assert result.document.artifact.parser_name == "text"
    assert [span.support_state for span in result.evidence_candidates] == [
        SupportState.EXTRACTED,
        SupportState.EXTRACTED,
    ]
    assert result.evidence_candidates[1].locator.line == 3
    assert (
        artifact_ledger.read_artifact(result.document.artifact.artifact_id)
        == result.document.artifact
    )
    stored = artifact_ledger.list_evidence("project:ingest")
    assert stored == result.evidence_candidates
    assert next(ledger.search_text("project:ingest", "정확도"))[1] == "목표 정확도는 94% 이상이다."
    ledger.close()


def test_object_store_rejects_wrong_digest(tmp_path: Path) -> None:
    store = ContentAddressedObjectStore(tmp_path / "workspace")

    with pytest.raises(ValueError, match="expected digest"):
        store.put(b"actual", "0" * 64, operation_id="operation:mismatch")

    with pytest.raises(ValueError, match="SHA-256"):
        store.path_for("../outside")
