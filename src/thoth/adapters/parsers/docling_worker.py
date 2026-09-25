"""Local isolated dependency worker. No ledger, credentials, network, or external plugins."""

import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    source = Path(sys.argv[2])
    output = Path(sys.argv[3])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OMP_NUM_THREADS"] = "4"
    original = socket.socket.connect

    def offline(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("PARSER_NETWORK_DISABLED")
        original(self, address)

    socket.socket.connect = offline
    try:
        manifest = json.loads((root / "asset-manifest.json").read_text())
        for name, digest in manifest["files"].items():
            path = (root / name).resolve()
            if (
                not path.is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("PARSER_ASSET_INTEGRITY_MISMATCH")
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
            TableStructureOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            artifacts_path=root,
            enable_remote_services=False,
            allow_external_plugins=False,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4),
            document_timeout=float(sys.argv[4]),
            do_ocr=False,
            do_table_structure=True,
            do_code_enrichment=False,
            do_formula_enrichment=False,
            table_structure_options=TableStructureOptions(
                mode=TableFormerMode.ACCURATE, do_cell_matching=True
            ),
        )
        result = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        ).convert(source)
        import importlib.metadata

        output.write_text(
            json.dumps(
                {
                    "document": result.document.export_to_dict(),
                    "status": str(result.status),
                    "errors": len(result.errors),
                    "docling_version": importlib.metadata.version("docling"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as error:
        output.write_text(json.dumps({"error_type": type(error).__name__}), encoding="utf-8")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
