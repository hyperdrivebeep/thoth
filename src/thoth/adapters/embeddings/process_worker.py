"""One owned process, priority queue, finite deadlines and confirmed shutdown."""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import JsonValue, TypeAdapter, ValidationError

from thoth.domain.embedding_index import (
    EmbeddingBudget,
    EmbeddingCapability,
    EmbeddingIndexError,
    EmbeddingVector,
)
from thoth.domain.model_asset import EMBEDDING_ERROR_CODES, EmbeddingAssetDescriptor
from thoth.domain.retrieval_embedding import MAX_PASSAGE_WINDOWS

_FRAME = TypeAdapter(dict[str, JsonValue])


@dataclass(order=True)
class _Request:
    priority: int
    sequence: int
    text: str = field(compare=False)
    role: Literal["QUERY", "PASSAGE"] = field(compare=False)
    deadline: float = field(compare=False)
    future: asyncio.Future[EmbeddingVector] = field(compare=False)
    window: int = field(default=0, compare=False)
    finished: asyncio.Event = field(default_factory=asyncio.Event, compare=False)
    started: bool = field(default=False, compare=False)
    cleanup_error: EmbeddingIndexError | None = field(default=None, compare=False)


class ProcessEmbeddingWorker:
    def __init__(
        self,
        factory: str,
        budget: EmbeddingBudget = EmbeddingBudget(),
        *,
        descriptor: EmbeddingAssetDescriptor | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", factory):
            raise ValueError("EMBEDDING_FACTORY_INVALID")
        self.factory, self.budget = factory, budget
        self.descriptor = descriptor
        self._loaded = False
        self._prepare_lock = asyncio.Lock()
        self.capability = EmbeddingCapability(
            process_tree="WINDOWS_JOB" if sys.platform == "win32" else "POSIX_SESSION",
            memory_limit="COMMIT" if sys.platform == "win32" else "ADDRESS_SPACE",
        )
        self._queue: asyncio.PriorityQueue[_Request] = asyncio.PriorityQueue()
        self._runner: asyncio.Task[None] | None = None
        self._process: Any = None
        self._stderr: threading.Thread | None = None
        self._closed = False
        self._sequence = 0
        self._queries = 0
        self._background = 0
        self.last_pid: int | None = None
        self.last_stop_confirmed = True
        self.last_exit_code: int | None = None
        self.encoder_pid: int | None = None
        self.memory_failure_observed = False
        # Passages with content past their last window in this process lifetime (diagnostic only).
        self.truncated_passages = 0

    def _start(self) -> None:
        if self._process is not None:
            return
        if self._closed:
            raise EmbeddingIndexError("EMBEDDING_WORKER_CLOSED")
        self.last_exit_code = None
        self.encoder_pid = None
        self.memory_failure_observed = False
        argv = (
            sys.executable,
            "-m",
            "thoth.adapters.embeddings.worker_main",
            self.factory,
            self.budget.model_dump_json(),
        )
        if self.descriptor is not None:
            argv += (self.descriptor.model_dump_json(),)
        env = dict(os.environ)
        # Trusted application factory, never a module path from a user/model payload.
        env["PYTHONPATH"] = os.pathsep.join(
            dict.fromkeys([str(Path(__file__).resolve().parents[3]), *sys.path])
        )
        env["PYTHONIOENCODING"] = "utf-8"
        if self.descriptor is not None:
            # ORT remains intra4/inter1; BLAS initialization must not reserve a machine-wide pool.
            threads = str(self.descriptor.load_budget.blas_threads)
            env.update(
                OPENBLAS_NUM_THREADS=threads,
                OMP_NUM_THREADS=threads,
                MKL_NUM_THREADS=threads,
                TOKENIZERS_PARALLELISM=str(
                    self.descriptor.load_budget.tokenizer_parallelism
                ).lower(),
            )
        if sys.platform == "win32":
            from thoth.adapters.models.windows_process_job import spawn_windows_job

            proc = spawn_windows_job(
                argv, env, Path.cwd(), memory_limit_bytes=self.budget.commit_limit_bytes
            )
        else:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        self._process = proc
        self.last_pid = proc.pid
        self.last_stop_confirmed = False

        def drain() -> None:
            try:
                assert proc.stderr is not None
                while chunk := proc.stderr.read(4096):
                    if any(
                        marker in chunk.lower()
                        for marker in (
                            b"bad_alloc",
                            b"memoryerror",
                            b"not enough memory",
                            b"memory allocation",
                            b"out of memory",
                        )
                    ):
                        self.memory_failure_observed = True
            except (OSError, ValueError):
                pass

        self._stderr = threading.Thread(target=drain, daemon=True)
        try:
            self._stderr.start()
        except BaseException:
            self._stderr = None
            self._stop()
            raise

    def _exchange(self, item: _Request, process: Any) -> EmbeddingVector:
        if process is None:
            raise EmbeddingIndexError("EMBEDDING_PROCESS_MISSING")
        raw = (
            json.dumps(
                {
                    "text": item.text,
                    "role": item.role,
                    **({"window": item.window} if item.window else {}),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        process.stdin.write(raw)
        process.stdin.flush()
        line = process.stdout.readline(16385)
        if not line or len(line) > 16384:
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED")
        try:
            value = _FRAME.validate_json(line)
        except ValidationError as exc:
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED") from exc
        if "error" in value:
            code = value["error"]
            raise EmbeddingIndexError(
                code
                if isinstance(code, str) and code in EMBEDDING_ERROR_CODES
                else "EMBEDDING_FAILED"
            )
        if self.descriptor is not None and (value.get("kind"), value.get("protocol")) != (
            "vector",
            2,
        ):
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED")
        data = value.get("data")
        if not isinstance(data, str):
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED")
        cut = value.get("truncated") is True
        tokens = value.get("tokens")
        sized = isinstance(tokens, int) and tokens >= 1
        vector = EmbeddingVector(
            bytes.fromhex(data),
            # A real encoder (descriptor protocol) sends the flag only for a passage with content
            # past its last window, so its silence means every token reached the model; a stand-in
            # worker says nothing either way. The size comes with the flag and with any passage
            # that needed more than one window.
            truncated=True if cut else (False if self.descriptor is not None else None),
            source_tokens=tokens if sized and isinstance(tokens, int) else None,
        )
        if vector.sha256 != value["sha256"]:
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED")
        if cut and item.window == 0:
            self.truncated_passages += 1
        return vector

    def _stop(self) -> None:
        self._loaded = False
        proc = self._process
        if proc is None:
            return
        self._process = None
        deadline = time.monotonic() + 3
        if sys.platform == "win32":
            ok = bool(proc.stop_and_confirm(deadline))
        else:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
                ok = True
            except subprocess.TimeoutExpired:
                ok = False
            for stream in [proc.stdin, proc.stdout, proc.stderr]:
                stream.close()
        if self._stderr is not None:
            self._stderr.join(timeout=max(0, deadline - time.monotonic()))
        self.last_stop_confirmed = ok and (self._stderr is None or not self._stderr.is_alive())
        if not self.last_stop_confirmed:
            self._closed = True
            raise EmbeddingIndexError("EMBEDDING_CLEANUP_UNCONFIRMED")

    async def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], window: int = 0
    ) -> EmbeddingVector:
        if self._closed:
            raise EmbeddingIndexError("EMBEDDING_WORKER_CLOSED")
        if not 0 <= window < MAX_PASSAGE_WINDOWS or (window and role != "PASSAGE"):
            raise EmbeddingIndexError("EMBEDDING_WINDOW_INVALID")
        if self.descriptor is not None and not self._loaded:
            raise EmbeddingIndexError("EMBEDDING_NOT_PREPARED")
        if len(text.encode()) > self.budget.max_input_bytes:
            raise EmbeddingIndexError("EMBEDDING_INPUT_TOO_LARGE")
        if role == "QUERY" and self._queries >= self.budget.query_queue:
            raise EmbeddingIndexError("EMBEDDING_QUEUE_FULL")
        if role == "PASSAGE" and self._background >= 1:
            raise EmbeddingIndexError("EMBEDDING_INDEX_BUSY")
        future = asyncio.get_running_loop().create_future()
        self._sequence += 1
        timeout = self.budget.query_timeout_ms if role == "QUERY" else self.budget.span_timeout_ms
        item = _Request(
            0 if role == "QUERY" else 1,
            self._sequence,
            text,
            role,
            time.monotonic() + timeout / 1000,
            future,
            window,
        )
        self._queries += role == "QUERY"
        self._background += role == "PASSAGE"
        self._queue.put_nowait(item)
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run())
        try:
            return await asyncio.wait_for(
                asyncio.shield(future), max(0.001, item.deadline - time.monotonic())
            )
        except TimeoutError as exc:
            future.cancel()
            if not item.started:
                item.finished.set()
            try:
                await asyncio.wait_for(item.finished.wait(), 3)
            except TimeoutError:
                raise EmbeddingIndexError("EMBEDDING_CLEANUP_UNCONFIRMED") from exc
            if item.cleanup_error is not None:
                raise item.cleanup_error from exc
            raise EmbeddingIndexError("EMBEDDING_TIMEOUT") from exc
        except asyncio.CancelledError:
            future.cancel()
            if not item.started:
                item.finished.set()
            try:
                await asyncio.wait_for(item.finished.wait(), 3)
            except TimeoutError as exc:
                raise EmbeddingIndexError("EMBEDDING_CLEANUP_UNCONFIRMED") from exc
            if item.cleanup_error is not None:
                raise item.cleanup_error from None
            raise

    async def _run(self) -> None:
        while not self._closed:
            item = await self._queue.get()
            self._queries -= item.role == "QUERY"
            self._background -= item.role == "PASSAGE"
            io: asyncio.Task[EmbeddingVector] | None = None
            owned_process: Any = None
            try:
                if item.future.cancelled():
                    continue
                remaining = item.deadline - time.monotonic()
                if remaining <= 0:
                    raise EmbeddingIndexError("EMBEDDING_TIMEOUT")
                if self.descriptor is not None and (not self._loaded or self._process is None):
                    raise EmbeddingIndexError("EMBEDDING_NOT_PREPARED")
                item.started = True
                self._start()
                owned_process = self._process
                io = asyncio.create_task(asyncio.to_thread(self._exchange, item, owned_process))
                # Cancellation must stop its process, not just its wait.
                while not io.done():
                    if item.future.cancelled():
                        raise EmbeddingIndexError("EMBEDDING_CANCELLED")
                    remaining = item.deadline - time.monotonic()
                    if remaining <= 0:
                        raise EmbeddingIndexError("EMBEDDING_TIMEOUT")
                    await asyncio.wait({io}, timeout=min(0.01, remaining))
                vector = io.result()
                if time.monotonic() >= item.deadline:
                    raise EmbeddingIndexError("EMBEDDING_TIMEOUT")
                if not item.future.done():
                    item.future.set_result(vector)
            except BaseException as exc:
                try:
                    if owned_process is not None and self._process is owned_process:
                        self._stop()
                except EmbeddingIndexError as cleanup_error:
                    item.cleanup_error = cleanup_error
                if io is not None:
                    with suppress(BaseException):
                        await asyncio.wait_for(asyncio.shield(io), 3)
                if not item.future.done():
                    item.future.set_exception(
                        item.cleanup_error or exc
                        if isinstance(exc, Exception)
                        else EmbeddingIndexError("EMBEDDING_CANCELLED")
                    )
                if isinstance(exc, asyncio.CancelledError):
                    raise
            finally:
                item.finished.set()
                self._queue.task_done()

    def close(self) -> None:
        self._closed = True
        while not self._queue.empty():
            item = self._queue.get_nowait()
            item.future.cancel()
            item.finished.set()
            self._queue.task_done()
        self._queries = 0
        self._background = 0
        if self._runner is not None:
            self._runner.cancel()
        self._stop()

    async def aclose(self) -> None:
        self.close()
        if self._runner is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._runner, 3)

    def _read_ready(self, process: Any) -> None:
        if process is None:
            raise EmbeddingIndexError("EMBEDDING_LOAD_FAILED")
        line = process.stdout.readline(16385)
        if not line or len(line) > 16384:
            self.last_exit_code = process.poll()
            raise EmbeddingIndexError("EMBEDDING_LOAD_FAILED")
        try:
            value = _FRAME.validate_json(line)
        except ValidationError as exc:
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED") from exc
        if "error" in value:
            code = value["error"]
            raise EmbeddingIndexError(
                code
                if isinstance(code, str) and code in EMBEDDING_ERROR_CODES
                else "EMBEDDING_LOAD_FAILED"
            )
        if (value.get("kind"), value.get("protocol")) != ("ready", 2):
            raise EmbeddingIndexError("EMBEDDING_LOAD_FAILED")
        pid = value.get("pid")
        if type(pid) is not int or pid <= 0:
            raise EmbeddingIndexError("EMBEDDING_LOAD_FAILED")
        self.encoder_pid = pid

    async def prepare(self, *, deadline: float | None = None) -> None:
        try:
            await self._prepare_with_deadline(deadline)
        except TimeoutError as exc:
            raise EmbeddingIndexError("EMBEDDING_TIMEOUT") from exc

    @property
    def prepared(self) -> bool:
        return self._loaded

    async def _prepare_with_deadline(self, deadline: float | None) -> None:
        if self.descriptor is None:
            return
        stop_at = time.monotonic() + self.descriptor.load_budget.timeout_ms / 1000
        if deadline is not None:
            stop_at = min(stop_at, deadline)
        async with asyncio.timeout_at(stop_at), self._prepare_lock:
            if self._closed:
                raise EmbeddingIndexError("EMBEDDING_WORKER_CLOSED")
            if self._loaded:
                return
            io: asyncio.Task[None] | None = None
            try:
                self._start()
                io = asyncio.create_task(asyncio.to_thread(self._read_ready, self._process))
                await asyncio.shield(io)
                if time.monotonic() >= stop_at:
                    raise EmbeddingIndexError("EMBEDDING_TIMEOUT")
                self._loaded = True
            except BaseException:
                self._stop()
                if io is not None:
                    with suppress(Exception):
                        await asyncio.wait_for(asyncio.shield(io), 3)
                raise
