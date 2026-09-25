from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from thoth.adapters.parsers import common
from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure


def _zip(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def test_hwpx_rejects_zip_slip(artifact_factory: Any) -> None:
    raw = _zip(
        {
            "META-INF/container.xml": b"<container />",
            "Contents/header.xml": b"<header />",
            "Contents/section0.xml": b"<section />",
            "../escape.txt": b"no",
        }
    )
    artifact = artifact_factory(raw, "application/hwp+zip", ".hwpx")

    with pytest.raises(ParserFailure) as captured:
        default_parser_registry().parse(artifact, raw)

    assert captured.value.code == ParserErrorCode.ZIP_SLIP


def test_archive_entry_size_limit_is_typed(
    artifact_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "MAX_SINGLE_ENTRY_BYTES", 4)
    raw = _zip(
        {
            "META-INF/container.xml": b"12345",
            "Contents/header.xml": b"<h/>",
            "Contents/section0.xml": b"<s/>",
        }
    )
    artifact = artifact_factory(raw, "application/hwp+zip", ".hwpx")

    with pytest.raises(ParserFailure) as captured:
        default_parser_registry().parse(artifact, raw)

    assert captured.value.code == ParserErrorCode.OVERSIZED_ARCHIVE


def test_declared_media_type_mismatch_is_typed(artifact_factory: Any) -> None:
    raw = b"%PDF-1.7\nnot really a PDF"
    artifact = artifact_factory(raw, "text/markdown", ".md")

    with pytest.raises(ParserFailure) as captured:
        default_parser_registry().parse(artifact, raw)

    assert captured.value.code == ParserErrorCode.MEDIA_TYPE_MISMATCH
