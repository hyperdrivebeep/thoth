from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Iterable
from pathlib import PurePosixPath

from thoth.domain.artifact import ArtifactEnvelope, StructuralNode
from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure

MAX_ARCHIVE_ENTRIES = 10_000
MAX_SINGLE_ENTRY_BYTES = 128 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def validate_byte_hash(artifact: ArtifactEnvelope, raw: bytes) -> None:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != artifact.byte_sha256.lower():
        raise ParserFailure(
            ParserErrorCode.BYTE_HASH_MISMATCH,
            f"artifact hash {artifact.byte_sha256} does not match input bytes {actual}",
        )


def safe_zip(raw: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "invalid ZIP container") from exc
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        archive.close()
        raise ParserFailure(ParserErrorCode.OVERSIZED_ARCHIVE, "archive has too many entries")
    total = 0
    for entry in entries:
        normalized = entry.filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            archive.close()
            raise ParserFailure(
                ParserErrorCode.ZIP_SLIP,
                f"unsafe archive member path: {entry.filename}",
            )
        if entry.file_size > MAX_SINGLE_ENTRY_BYTES:
            archive.close()
            raise ParserFailure(
                ParserErrorCode.OVERSIZED_ARCHIVE,
                f"archive member exceeds limit: {entry.filename}",
            )
        total += entry.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            archive.close()
            raise ParserFailure(
                ParserErrorCode.OVERSIZED_ARCHIVE,
                "archive uncompressed size exceeds limit",
            )
    return archive


def parser_artifact(artifact: ArtifactEnvelope, *, name: str, version: str) -> ArtifactEnvelope:
    return artifact.model_copy(update={"parser_name": name, "parser_version": version})


def node_id(artifact: ArtifactEnvelope, ordinal: int, kind: str) -> str:
    payload = f"{artifact.artifact_id}\0{ordinal}\0{kind}".encode()
    return f"node:{hashlib.sha256(payload).hexdigest()[:24]}"


def coverage(nodes: Iterable[StructuralNode]) -> str:
    material = [node for node in nodes if node.text and node.text.strip()]
    return "FULL" if material else "NONE"
