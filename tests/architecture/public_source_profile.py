"""Validate source-only package bytes; this is not a FULL verification authority."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_ANCHORS = (
    "PROJECT_WIKI/NOW.md",
    "PROJECT_WIKI/50_SEED_ROADMAP/behavioral-acceptance-contracts.md",
    "config/architecture-conformance.json",
    "docs/PACKAGING.md",
    "docs/VERIFICATION.md",
    "docs/architecture/backend-runtime-boundaries.md",
)
_EXCLUDED_FILES = (
    "research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md",
    "PROJECT_WIKI/80_OPEN_QUESTIONS/INDEX.md",
    "PROJECT_WIKI/50_SEED_ROADMAP/seed-map.md",
    "PROJECT_WIKI/50_SEED_ROADMAP/seed-1-memory-kernel.md",
    "PROJECT_WIKI/40_HACKATHON_DEMO/demo-scope.md",
)


class PublicPackageError(ValueError):
    """A declared public package path or byte contract does not match the tree."""


class _FileRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def canonical_relative_path(cls, value: str) -> str:
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or ":" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).as_posix() != value
        ):
            raise ValueError("public manifest path is not a canonical relative path")
        return value


class _SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["thoth-source-preview-manifest-v1"]
    snapshot_type: Literal["MIT source-only public preview"]
    created_at: str
    license: Literal["MIT"]
    git_history_included: bool
    full_suite_verified: bool
    known_selected_regression_failures: int = Field(ge=0)
    source_archive_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    public_repository: str
    verification_basis: str
    files: tuple[_FileRow, ...] = Field(min_length=1)


class _OwnerDebt(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    status: str


class _CanonicalOwner(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    atomicity_debt: _OwnerDebt | None = None


class _ArchitectureOwners(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    canonical_owners: tuple[_CanonicalOwner, ...]


@dataclass(frozen=True, slots=True)
class PublicSourceProfile:
    root: Path
    manifest: _SourceManifest
    files: Mapping[str, _FileRow]

    def read_bytes(self, relative: str) -> bytes:
        row = self.files.get(relative)
        if row is None:
            raise PublicPackageError(f"required public anchor is not listed: {relative}")
        path = self.root / relative
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PublicPackageError(f"listed public file is unreadable: {relative}") from exc
        if len(data) != row.bytes or hashlib.sha256(data).hexdigest() != row.sha256:
            raise PublicPackageError(f"listed public file bytes differ: {relative}")
        return data

    def read_text(self, relative: str) -> str:
        return self.read_bytes(relative).decode("utf-8")


def public_source_profile(root: Path) -> PublicSourceProfile | None:
    """Return a byte-checked public profile, or None only for a tree without a manifest."""
    root = root.resolve()
    manifest_path = root / "SOURCE_MANIFEST.json"
    if not manifest_path.exists():
        if manifest_path.is_symlink():
            raise PublicPackageError("public manifest symlink is broken")
        return None
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise PublicPackageError("public manifest is not a regular file")
    try:
        manifest = _SourceManifest.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise PublicPackageError("public manifest schema or type is invalid") from exc
    paths = [row.path.casefold() for row in manifest.files]
    if len(paths) != len(set(paths)):
        raise PublicPackageError("public manifest paths are duplicated")
    files = MappingProxyType({row.path: row for row in manifest.files})
    profile = PublicSourceProfile(root=root, manifest=manifest, files=files)
    for row in manifest.files:
        path = root / row.path
        try:
            resolved = path.resolve(strict=True)
            if path.is_symlink() or not resolved.is_relative_to(root) or not path.is_file():
                raise PublicPackageError(f"listed public path is redirected: {row.path}")
        except OSError as exc:
            raise PublicPackageError(f"listed public file is missing: {row.path}") from exc
        profile.read_bytes(row.path)
    for relative in _ANCHORS:
        profile.read_bytes(relative)
    for relative in _EXCLUDED_FILES:
        if relative in files or (root / relative).exists() or (root / relative).is_symlink():
            raise PublicPackageError(f"excluded internal path is present: {relative}")
    if (root / "deploy").exists() or (root / "deploy").is_symlink():
        raise PublicPackageError("deployment scaffold is present in source-only package")
    if any(path.startswith("deploy/") for path in files):
        raise PublicPackageError("deployment path is listed in source-only package")
    if "deployment configuration" not in profile.read_text("docs/PACKAGING.md"):
        raise PublicPackageError("public packaging exclusion is missing")
    if "HOSTED_IMAGE_NOT_VERIFIED" not in profile.read_text("docs/PACKAGING.md"):
        raise PublicPackageError("hosted image status is not explicit")
    return profile


def current_open_owner_debt(profile: PublicSourceProfile) -> int:
    """Count OPEN canonical owners from the hash-checked public architecture record."""
    try:
        document = _ArchitectureOwners.model_validate_json(
            profile.read_bytes("config/architecture-conformance.json")
        )
    except ValidationError as exc:
        raise PublicPackageError("canonical owner record is invalid") from exc
    return sum(
        owner.atomicity_debt is not None and owner.atomicity_debt.status == "OPEN"
        for owner in document.canonical_owners
    )
