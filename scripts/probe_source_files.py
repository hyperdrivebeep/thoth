from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass


def probe(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    artifact = ArtifactEnvelope(
        artifact_id=f"artifact:probe-{digest[:16]}",
        project_id="project:source-probe",
        source_uri=path.as_uri(),
        media_type="application/octet-stream",
        byte_sha256=digest,
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.PUBLIC,
        retrieved_at=datetime.now(UTC),
        parser_name="unparsed",
        parser_version="0",
    )
    document = default_parser_registry().parse(artifact, raw, source_path=path)
    material_nodes = tuple(node for node in document.nodes if node.text)
    return {
        "path": str(path.resolve()),
        "byte_sha256": digest,
        "bytes": len(raw),
        "parser": document.artifact.parser_name,
        "coverage": document.extraction_coverage,
        "node_count": len(document.nodes),
        "material_node_count": len(material_nodes),
        "warning_codes": [warning.code.value for warning in document.warnings],
        "first_locator": (
            None if not material_nodes else material_nodes[0].locator.model_dump(mode="json")
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    arguments = parser.parse_args()
    results = [probe(path) for path in arguments.paths]
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
