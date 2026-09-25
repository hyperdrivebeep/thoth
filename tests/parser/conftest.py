from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass


@pytest.fixture
def artifact_factory():  # type: ignore[no-untyped-def]
    def build(raw: bytes, media_type: str, suffix: str = "") -> ArtifactEnvelope:
        return ArtifactEnvelope(
            artifact_id=f"artifact:{hashlib.sha256(raw).hexdigest()[:16]}",
            project_id="project:test",
            source_uri=f"fixture{suffix}",
            media_type=media_type,
            byte_sha256=hashlib.sha256(raw).hexdigest(),
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            retrieved_at=datetime(2026, 8, 30, tzinfo=UTC),
            parser_name="unparsed",
            parser_version="0",
        )

    return build
