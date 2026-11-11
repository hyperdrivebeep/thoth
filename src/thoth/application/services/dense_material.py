"""Dense scores for the research path: a resident window matrix and one question vector.

A project that switched local retrieval on and has a READY index gets a DenseMaterial for its
question. Every other case gets nothing (a project that is off) or a material that names why dense
cannot be used, and the routing policy then falls back to RRF(LEX, GB) with that reason. The
numpy-free reference for a passage score is embedding_index.max_window_score.
"""

import asyncio
import importlib
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.domain.embedding_index import EmbeddingIndexError
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.retrieval_embedding import (
    DenseMaterial,
    DenseScore,
    DenseUnavailableReason,
    dense_question_digest,
    dense_source_digest,
)

# One window is 384 float64 values. 128 MiB holds 43,690 windows, about three times the largest
# corpus measured (14.5k windows for 7.7k passages).
WINDOW_BYTES = 384 * 8
RESIDENT_MATRIX_BYTE_CAP = 128 * 1024 * 1024
RESIDENT_PROJECT_CAP = 2
# DenseMaterial carries at most this many scores.
MAX_DENSE_SPANS = 20_000
WARM_COOLDOWN_SECONDS = 60.0
BUILD_WAIT_SECONDS = 5.0

IndexStamp = tuple[bool, int, tuple[tuple[str, int], ...]]


@runtime_checkable
class IndexStampSource(Protocol):
    def index_stamp(self, project: str) -> IndexStamp: ...


class DenseMaterialSource(Protocol):
    async def material(
        self, project_id: str, question: str, evidence: tuple[EvidenceSpan, ...]
    ) -> DenseMaterial | None: ...


class _Unavailable(Exception):
    def __init__(self, reason: DenseUnavailableReason) -> None:
        super().__init__(reason)
        self.reason: DenseUnavailableReason = reason


@dataclass(frozen=True)
class Resident:
    stamp: IndexStamp
    matrix: Any  # float64 [windows, 384], rows of one passage are adjacent
    starts: Any  # int64 [passages], first row of each passage
    ends: Any  # int64 [passages], one past its last row
    rows: dict[str, int]
    shas: list[str]


def load_numpy() -> Any:
    return importlib.import_module("numpy")


def score_decimal(value: float) -> Decimal:
    """A score as DenseScore stores it: inside [-1, 1] and rounded to nine places."""
    return Decimal(f"{max(-1.0, min(1.0, value)):.9f}")


def dense_scores(spans: tuple[EvidenceSpan, ...], values: list[float]) -> tuple[DenseScore, ...]:
    """One score per span. The only place a score skips validation: score_decimal has already
    clamped and rounded it, and the spans are distinct eligible spans."""
    return tuple(
        DenseScore.model_construct(span_id=s.span_id, score=score_decimal(v))
        for s, v in zip(spans, values, strict=True)
    )


class DenseMaterialProvider:
    def __init__(
        self,
        *,
        stamp: Callable[[str], IndexStamp],
        index: Callable[[], EmbeddingIndexService | None],
        warm: Callable[[], Awaitable[None]] | None = None,
        numpy_loader: Callable[[], Any] = load_numpy,
        byte_cap: int = RESIDENT_MATRIX_BYTE_CAP,
        clock: Callable[[], float] = time.monotonic,
        build_wait: float = BUILD_WAIT_SECONDS,
    ) -> None:
        self._stamp, self._index, self._warm = stamp, index, warm
        self._numpy_loader, self._byte_cap = numpy_loader, byte_cap
        self._clock, self._build_wait = clock, build_wait
        self._residents: dict[str, Resident] = {}
        self._failed: dict[str, tuple[IndexStamp, DenseUnavailableReason]] = {}
        self._building: dict[str, tuple[IndexStamp, asyncio.Task[Resident]]] = {}
        self.warming: asyncio.Task[None] | None = None
        self._cooldown_until = 0.0

    # --- resident copy -------------------------------------------------------------------

    def resident_projects(self) -> tuple[str, ...]:
        return tuple(self._residents)

    def resident_bytes(self) -> int:
        return sum(int(r.matrix.nbytes) for r in self._residents.values())

    def invalidate(self, project: str | None = None) -> None:
        """Drop the resident copy (and any build) of one project, or of all of them."""
        names = [project] if project is not None else [*self._residents, *self._building]
        for name in names:
            self._residents.pop(name, None)
            self._failed.pop(name, None)
            building = self._building.pop(name, None)
            if building is not None:
                building[1].cancel()

    def _assemble(
        self,
        np: Any,
        index: EmbeddingIndexService,
        project: str,
        stamp: IndexStamp,
        stop: threading.Event,
    ) -> Resident:
        """The blocking half of a build: every SQLite read and the matrix itself.

        It runs in a worker thread so the event loop keeps serving other requests while a large
        project is read, and it looks at stop between sources so a dropped build ends early.
        """
        chunks: list[bytes] = []
        rows: dict[str, int] = {}
        shas: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        offset = 0
        for artifact, version in index.ready_sources(project):
            if stop.is_set():
                raise _Unavailable("INDEX_PENDING")
            for span_id, sha, windows in index.read_source_windows(project, artifact, version):
                if span_id in rows:
                    continue
                if (offset + len(windows)) * WINDOW_BYTES > self._byte_cap:
                    raise _Unavailable("INDEX_TOO_LARGE")
                rows[span_id] = len(shas)
                shas.append(sha)
                starts.append(offset)
                offset += len(windows)
                ends.append(offset)
                chunks.append(b"".join(w.data for w in windows))
        if not shas:
            raise _Unavailable("INDEX_PENDING")
        matrix = np.frombuffer(b"".join(chunks), dtype="<f4").reshape(-1, 384).astype(np.float64)
        return Resident(
            stamp,
            matrix,
            np.asarray(starts, dtype=np.int64),
            np.asarray(ends, dtype=np.int64),
            rows,
            shas,
        )

    async def _build(
        self, project: str, stamp: IndexStamp, index: EmbeddingIndexService
    ) -> Resident:
        np = self._numpy_loader()
        stop = threading.Event()
        try:
            resident = await asyncio.to_thread(self._assemble, np, index, project, stamp, stop)
        except asyncio.CancelledError:
            stop.set()
            raise
        self._residents.pop(project, None)
        self._residents[project] = resident
        while len(self._residents) > RESIDENT_PROJECT_CAP:
            del self._residents[next(iter(self._residents))]
        return resident

    async def _resident(
        self, project: str, stamp: IndexStamp, index: EmbeddingIndexService
    ) -> Resident:
        current = self._residents.get(project)
        if current is not None and current.stamp == stamp:
            return current
        failed = self._failed.get(project)
        if failed is not None and failed[0] == stamp:
            raise _Unavailable(failed[1])
        self._residents.pop(project, None)
        building = self._building.get(project)
        if building is None or building[0] != stamp or building[1].done():
            if building is not None:
                building[1].cancel()
            task = asyncio.create_task(self._build(project, stamp, index))
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            self._building[project] = building = (stamp, task)
        try:
            return await asyncio.wait_for(asyncio.shield(building[1]), self._build_wait)
        except TimeoutError:
            raise _Unavailable("INDEX_PENDING") from None
        except asyncio.CancelledError:
            if building[1].cancelled():
                # The index changed under the build (invalidate); this request is still alive.
                raise _Unavailable("INDEX_PENDING") from None
            raise
        except _Unavailable as exc:
            self._failed[project] = (stamp, exc.reason)
            raise
        except EmbeddingIndexError as exc:
            reason: DenseUnavailableReason = (
                "INDEX_STALE" if str(exc) == "EMBEDDING_SOURCE_CHANGED" else "INDEX_INCOMPLETE"
            )
            self._failed[project] = (stamp, reason)
            raise _Unavailable(reason) from None

    # --- one question --------------------------------------------------------------------

    def _start_warm(self) -> None:
        if self._warm is None or self._clock() < self._cooldown_until:
            return
        if self.warming is not None and not self.warming.done():
            return
        self.warming = asyncio.create_task(self._run_warm(self._warm))

    async def _run_warm(self, warm: Callable[[], Awaitable[None]]) -> None:
        try:
            await warm()
        except Exception:
            pass
        finally:
            # A worker that is still not up is retried later, not on every request.
            self._cooldown_until = self._clock() + WARM_COOLDOWN_SECONDS

    @staticmethod
    def _rows(resident: Resident, eligible: tuple[EvidenceSpan, ...]) -> list[int]:
        rows, shas = resident.rows, resident.shas
        picked: list[int] = []
        for span in eligible:
            row = rows.get(span.span_id)
            if row is None:
                raise _Unavailable("INDEX_INCOMPLETE")
            if shas[row] != span.text_sha256:
                raise _Unavailable("INDEX_STALE")
            picked.append(row)
        return picked

    @staticmethod
    def best_scores(np: Any, resident: Resident, query: Any, picked: list[int]) -> list[float]:
        """Best window score of each picked passage, equal to max_window_score before rounding.

        The matrix product sums in another order than the reference, so a value that sits within
        1e-12 of a rounding boundary or of zero is computed again the reference way.
        """
        best = np.clip(np.maximum.reduceat(resident.matrix @ query, resident.starts), -1.0, 1.0)
        chosen = best[np.asarray(picked, dtype=np.int64)]
        scaled = chosen * 1e9
        close = (np.abs(scaled - np.floor(scaled) - 0.5) < 1e-3) | (np.abs(chosen) < 1e-12)
        values: list[float] = chosen.tolist()
        q = query.tolist()
        positions: list[int] = np.nonzero(close)[0].tolist()
        for position in positions:
            row = picked[position]
            windows = resident.matrix[resident.starts[row] : resident.ends[row]].tolist()
            values[position] = max(sum(a * b for a, b in zip(q, w, strict=True)) for w in windows)
        return values

    async def _query_vector(self, np: Any, index: EmbeddingIndexService, question: str) -> Any:
        try:
            vector = await index.worker.encode_one(question, role="QUERY")
        except EmbeddingIndexError as exc:
            code = str(exc)
            if code == "EMBEDDING_NOT_PREPARED":
                self._start_warm()
            raise _Unavailable(
                "EMBEDDING_TIMEOUT"
                if code == "EMBEDDING_TIMEOUT"
                else "QUERY_TOO_LONG"
                if code == "EMBEDDING_INPUT_TOO_LARGE"
                else "EMBEDDING_NOT_PREPARED"
                if code == "EMBEDDING_NOT_PREPARED"
                else "EMBEDDING_FAILED"
            ) from None
        return np.frombuffer(vector.data, dtype="<f4").astype(np.float64)

    async def _compute(
        self,
        project: str,
        question: str,
        eligible: tuple[EvidenceSpan, ...],
        stamp: IndexStamp,
    ) -> list[float]:
        if len(eligible) > MAX_DENSE_SPANS:
            raise _Unavailable("INDEX_TOO_LARGE")
        try:
            np = self._numpy_loader()
        except ImportError:
            raise _Unavailable("EMBEDDING_DEPENDENCY_UNAVAILABLE") from None
        if not stamp[2]:
            raise _Unavailable("INDEX_PENDING")
        index = self._index()
        if index is None or not getattr(index.worker, "prepared", True):
            self._start_warm()
            raise _Unavailable("EMBEDDING_NOT_PREPARED")
        if index.on_change is None:
            index.on_change = self.invalidate
        resident = await self._resident(project, stamp, index)
        picked = self._rows(resident, eligible)
        query = await self._query_vector(np, index, question)
        return self.best_scores(np, resident, query, picked)

    def _unavailable(
        self,
        project: str,
        question: str,
        eligible: tuple[EvidenceSpan, ...],
        reason: DenseUnavailableReason,
    ) -> DenseMaterial:
        return DenseMaterial.prepared(
            project_id=project,
            question_sha256=dense_question_digest(question),
            source_digest=dense_source_digest(eligible),
            unavailable_reason=reason,
        )

    async def material(
        self, project_id: str, question: str, evidence: tuple[EvidenceSpan, ...]
    ) -> DenseMaterial | None:
        """The material for one question, None for a project that is off or has nothing to route.

        It never raises: whatever goes wrong is a reason, so the request goes on without dense.
        """
        eligible = connected_retrieval_spans(evidence)
        if len({s.artifact_id for s in eligible}) <= 1:
            return None  # a single source is routed to the lexical lane, which ignores dense
        if {s.project_id for s in eligible} != {project_id}:
            return None  # routing refuses a mixed frame itself
        try:
            stamp = self._stamp(project_id)
        except Exception:
            return self._unavailable(project_id, question, eligible, "EMBEDDING_FAILED")
        if not stamp[0]:
            self.invalidate(project_id)
            return None
        try:
            values = await self._compute(project_id, question, eligible, stamp)
        except _Unavailable as exc:
            return self._unavailable(project_id, question, eligible, exc.reason)
        except Exception:
            return self._unavailable(project_id, question, eligible, "EMBEDDING_FAILED")
        return DenseMaterial.prepared(
            project_id=project_id,
            question_sha256=dense_question_digest(question),
            source_digest=dense_source_digest(eligible),
            scores=dense_scores(eligible, values),
        )
