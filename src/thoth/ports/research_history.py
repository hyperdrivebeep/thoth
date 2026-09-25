"""Bounded immutable reads on the existing canonical owners."""

from dataclasses import dataclass
from typing import Literal, Protocol

from thoth.domain.memory import FullMemoryRevision
from thoth.domain.research_basis import SourceBasisRef


@dataclass(frozen=True)
class HistoryWatermark:
    revisions: int
    memories: int


@dataclass(frozen=True)
class HistoryRow:
    owner_kind: Literal["SEMANTIC_REVISION", "FULL_MEMORY"]
    immutable_id: str
    revision_digest: str
    occurred_at: str

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return self.occurred_at, self.owner_kind, self.immutable_id


class ResearchHistoryReadPort(Protocol):
    def watermark(self, project_id: str) -> HistoryWatermark: ...
    def page(
        self,
        project_id: str,
        watermark: HistoryWatermark,
        after: tuple[str, str, str] | None,
        limit: int,
    ) -> tuple[HistoryRow, ...]: ...
    def read_memory(self, project_id: str, digest: str) -> FullMemoryRevision | None: ...
    def read_immutable_span_basis(self, project_id: str, span_id: str) -> SourceBasisRef | None: ...


class HistoryCursorCodecPort(Protocol):
    def encode(self, payload: str) -> str: ...
    def decode(self, token: str) -> str: ...
