"""Explicit structured PDF adapter with pre-provisioned assets and a bounded local worker."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from pypdf import PdfReader

from thoth.adapters.parsers.common import validate_byte_hash
from thoth.adapters.parsers.docling_structure import convert_structure
from thoth.adapters.parsers.document_time import (
    PdfReaderLike,
    extract_declared_document_time,
    extract_pdf_time_observations,
)
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure


class DoclingPdfParser:
    name = "docling-pdf"
    version = "1.2.0+docling2.127.0"
    media_types = frozenset({"application/pdf"})
    suffixes = frozenset({".pdf"})
    capabilities = frozenset({"TEXT", "TABLE", "HEADER", "CAPTION", "UNIT", "FOOTNOTE", "MENTION"})

    def __init__(
        self, assets: Path | None, *, python: str | None = None, timeout_seconds: float = 120
    ) -> None:
        self.assets = assets
        self.python = python or sys.executable
        self.timeout_seconds = timeout_seconds

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        if self.assets is None or not (self.assets / "asset-manifest.json").is_file():
            raise ParserFailure(
                ParserErrorCode.PARSER_CAPABILITY_UNAVAILABLE,
                "structured parser assets were not prepared",
            )
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
        except Exception as error:
            raise ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "invalid PDF document") from error
        if reader.is_encrypted:
            raise ParserFailure(ParserErrorCode.ENCRYPTED_DOCUMENT, "encrypted PDF is held")
        asset_digest = hashlib.sha256(
            (self.assets / "asset-manifest.json").read_bytes()
        ).hexdigest()
        config = {
            "adapter": self.name,
            "version": self.version,
            "ocr": False,
            "table_mode": "accurate",
            "cell_matching": True,
            "cpu_threads": 4,
            "timeout_seconds": self.timeout_seconds,
            "network": "DISABLED",
        }
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="thoth-structured-") as directory:
            root = Path(directory)
            source = root / "source.pdf"
            output = root / "structure.json"
            source.write_bytes(raw)
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "4"}
            try:
                process = subprocess.run(
                    [
                        self.python,
                        "-m",
                        "thoth.adapters.parsers.docling_worker",
                        str(self.assets.resolve()),
                        str(source),
                        str(output),
                        str(self.timeout_seconds),
                    ],
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0,
                )
            except subprocess.TimeoutExpired as error:
                raise ParserFailure(
                    ParserErrorCode.PARSER_DEADLINE, "structured parser exceeded its local deadline"
                ) from error
            if process.returncode != 0 or not output.is_file():
                raise ParserFailure(
                    ParserErrorCode.PARSER_CAPABILITY_UNAVAILABLE,
                    "structured parser failed; no alternative parser was selected",
                )
            result = json.loads(output.read_text(encoding="utf-8"))
        if result.get("docling_version") != "2.127.0":
            raise ParserFailure(
                ParserErrorCode.PARSER_CAPABILITY_UNAVAILABLE,
                "structured dependency version mismatch",
            )
        document = convert_structure(
            artifact,
            result["document"],
            parser_name=self.name,
            parser_version=self.version,
            configuration_digest=digest,
            asset_digest=asset_digest,
            partial=result["status"] != "ConversionStatus.SUCCESS" or bool(result["errors"]),
        )
        observations = (
            *extract_pdf_time_observations(cast(PdfReaderLike, reader)),
            *extract_declared_document_time(document.nodes),
        )
        return document.model_copy(update={"document_time_observations": observations})
