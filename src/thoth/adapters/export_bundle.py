from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import orjson

from thoth.domain.export_snapshot import ExportFile, FrozenExportSnapshot, StagedExportBundle

_FILES = ("canonical.json", "manifest.json", "ro-crate-metadata.json")


class FilesystemExportBundle:
    """Stage fresh immutable bundles. A failed retry never overwrites an earlier bundle."""

    def __init__(self, workspace: Path) -> None:
        self._root = (workspace / "exports").resolve()

    def stage(self, snapshot: FrozenExportSnapshot, snapshot_digest: str) -> StagedExportBundle:
        self._root.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="bundle-", dir=self._root)).resolve()
        if not root.is_relative_to(self._root):
            raise ValueError("EXPORT_PATH_ESCAPED")
        included = tuple(item for item in snapshot.resources if item.inclusion_mode == "FULL")
        canonical: dict[str, object] = {
            "schema_version": snapshot.schema_version,
            "project_id": snapshot.project_id,
            "snapshot_digest": snapshot_digest,
            "plan_digest": snapshot.plan_digest,
            "scope_refs": snapshot.scope.scope_refs,
            "head_set": snapshot.selected_revisions,
            "recipient": snapshot.scope.recipient,
            "trust_boundary": snapshot.scope.trust_boundary,
            "resources": tuple(
                {
                    "artifact_id": item.artifact.artifact_id,
                    "digest": item.artifact.byte_sha256,
                    "provenance": item.artifact.source_uri,
                    "rights": "UNKNOWN" if item.source is None else item.source.rights,
                    "classification": item.artifact.security_class.value,
                    "source_digest": None if item.source is None else item.source.source_digest,
                    "source_bindings": tuple(
                        {
                            "source_id": source.source_id,
                            "digest": source.source_digest,
                            "rights": source.rights,
                            "classification": source.security_class,
                        }
                        for source in item.source_bindings
                    ),
                }
                for item in included
            ),
            # Excluded resources do not expose their paths or source metadata in the package.
            "excluded_resource_count": len(snapshot.resources) - len(included),
            "semantic_truth_certified": False,
        }
        self._write(root / "canonical.json", canonical)
        canonical_digest = self._digest(root / "canonical.json")
        manifest: dict[str, object] = {
            "algorithm": "sha256",
            "files": ({"path": "canonical.json", "digest": canonical_digest},),
            "snapshot_digest": snapshot_digest,
            "plan_digest": snapshot.plan_digest,
        }
        self._write(root / "manifest.json", manifest)
        self._write(
            root / "ro-crate-metadata.json",
            {
                "@context": "https://w3id.org/ro/crate/1.1/context",
                "@graph": (
                    {"@id": "./", "@type": "Dataset", "hasPart": [{"@id": "canonical.json"}]},
                    {"@id": "canonical.json", "@type": "File", "sha256": canonical_digest},
                ),
            },
        )
        return StagedExportBundle(
            root=str(root),
            artifacts=tuple(
                ExportFile(
                    path=name, digest=self._digest(root / name), size=(root / name).stat().st_size
                )
                for name in _FILES
            ),
            manifest=manifest,
        )

    def verify(self, bundle: StagedExportBundle) -> tuple[bool, tuple[str, ...]]:
        root = Path(bundle.root).resolve()
        if not root.is_relative_to(self._root) or root == self._root:
            return False, ("EXPORT_ROOT_INVALID",)
        if len(bundle.artifacts) != len(_FILES) or {item.path for item in bundle.artifacts} != set(
            _FILES
        ):
            return False, ("EXPORT_FILE_SET_INVALID",)
        failed: list[str] = []
        for item in bundle.artifacts:
            path = (root / item.path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                failed.append(item.path)
                continue
            if path.stat().st_size != item.size or self._digest(path) != item.digest:
                failed.append(item.path)
        return not failed, tuple(failed)

    @staticmethod
    def _write(path: Path, payload: dict[str, object]) -> None:
        # Exclusive file creation even inside the fresh staging directory.
        with path.open("xb") as handle:
            handle.write(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
