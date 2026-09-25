from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.domain.artifact import (
    ArtifactEnvelope,
    ParseIssue,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind
from thoth.domain.errors import ParserFailure


class GitEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    mode: str
    object_id: str
    stage: int = Field(ge=0)
    status: str | None = None


class GitManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    head: str
    dirty: bool
    entries: tuple[GitEntry, ...]


class GitManifestParser:
    name = "git"
    version = "1.0.0"
    media_types = frozenset({"application/vnd.thoth.git-manifest+json"})
    suffixes = frozenset({".git-manifest.json"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        try:
            loaded = cast(object, json.loads(raw.decode("utf-8")))
            manifest = GitManifest.model_validate(loaded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ParserFailure(
                ParserErrorCode.CORRUPT_DOCUMENT, "invalid Git evidence manifest"
            ) from exc
        warnings = (
            (
                ParseIssue(
                    code=ParserErrorCode.DIRTY_GIT_TARGET,
                    message="repository had uncommitted changes when the manifest was captured",
                ),
            )
            if manifest.dirty
            else ()
        )
        nodes = tuple(
            StructuralNode(
                node_id=node_id(artifact, index, "PARAGRAPH"),
                artifact_id=artifact.artifact_id,
                kind=StructuralNodeKind.PARAGRAPH,
                ordinal=index,
                text=(
                    f"mode={entry.mode}; object={entry.object_id}; stage={entry.stage}; "
                    f"status={entry.status or 'TRACKED'}"
                ),
                locator=SourceLocator(file_path=entry.path),
            )
            for index, entry in enumerate(manifest.entries)
        )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=nodes,
            extraction_coverage=coverage(nodes),
            warnings=warnings,
        )
