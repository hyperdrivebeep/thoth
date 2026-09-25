from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from thoth.domain.artifact import ArtifactEnvelope, ParserSelection, StructuralDocument


@runtime_checkable
class ParserCapabilityPort(Protocol):
    capabilities: frozenset[str]


@runtime_checkable
class AsyncParserRegistryPort(Protocol):
    async def parse_async(
        self,
        artifact: ArtifactEnvelope,
        raw: bytes,
        *,
        source_path: Path | None = None,
        selection: ParserSelection | None = None,
    ) -> StructuralDocument: ...


class ParserPort(Protocol):
    name: str
    version: str
    media_types: frozenset[str]
    suffixes: frozenset[str]

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument: ...


class ParserRegistryPort(Protocol):
    def supported_media_types(self) -> Sequence[str]: ...

    def parse(
        self,
        artifact: ArtifactEnvelope,
        raw: bytes,
        *,
        source_path: Path | None = None,
        selection: ParserSelection | None = None,
    ) -> StructuralDocument: ...
