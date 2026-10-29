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

from thoth.domain.embedding_index import (
    EmbeddingBudget,
    EmbeddingCapability,
    EmbeddingIndexError,
    EmbeddingVector,
)


@dataclass(order=True)
class _Request:
    priority: int
    sequence: int
    text: str = field(compare=False)
    role: Literal["QUERY", "PASSAGE"] = field(compare=False)
    deadline: float = field(compare=False)
    future: asyncio.Future[EmbeddingVector] = field(compare=False)
    finished: asyncio.Event = field(default_factory=asyncio.Event, compare=False)
    started: bool = field(default=False, compare=False)
    cleanup_error: EmbeddingIndexError | None = field(default=None, compare=False)


class ProcessEmbeddingWorker:
    def __init__(self, factory: str, budget: EmbeddingBudget = EmbeddingBudget()) -> None:
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", factory):
            raise ValueError("EMBEDDING_FACTORY_INVALID")
        self.factory, self.budget = factory, budget
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

    def _start(self) -> None:
        if self._process is not None:
            return
        if self._closed:
            raise EmbeddingIndexError("EMBEDDING_WORKER_CLOSED")
        argv = (
            sys.executable,
            "-m",
            "thoth.adapters.embeddings.worker_main",
            self.factory,
            self.budget.model_dump_json(),
        )
        env = dict(os.environ)
        # Trusted application factory, never a module path from a user/model payload.
        env["PYTHONPATH"] = os.pathsep.join(
            dict.fromkeys([str(Path(__file__).resolve().parents[3]), *sys.path])
        )
        env["PYTHONIOENCODING"] = "utf-8"
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
                while proc.stderr.read(4096):
                    pass
            except (OSError, ValueError):
                pass

        self._stderr = threading.Thread(target=drain, daemon=True)
        self._stderr.start()

    def _exchange(self, item: _Request) -> EmbeddingVector:
        process = self._process
        if process is None:
            raise EmbeddingIndexError("EMBEDDING_PROCESS_MISSING")
        raw = (
            json.dumps(
                {"text": item.text, "role": item.role}, ensure_ascii=False, separators=(",", ":")
            ).encode()
            + b"\n"
        )
        process.stdin.write(raw)
        process.stdin.flush()
        line = process.stdout.readline(16385)
        if not line or len(line) > 16384:
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED")
        value = json.loads(line)
        if "error" in value:
            raise EmbeddingIndexError("EMBEDDING_ENCODER_FAILED")
        vector = EmbeddingVector(bytes.fromhex(value["data"]))
        if vector.sha256 != value["sha256"]:
            raise EmbeddingIndexError("EMBEDDING_PROTOCOL_FAILED")
        return vector

    def _stop(self) -> None:
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

    async def encode_one(self, text: str, *, role: Literal["QUERY", "PASSAGE"]) -> EmbeddingVector:
        if self._closed:
            raise EmbeddingIndexError("EMBEDDING_WORKER_CLOSED")
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
            try:
                if item.future.cancelled():
                    continue
                remaining = item.deadline - time.monotonic()
                if remaining <= 0:
                    raise EmbeddingIndexError("EMBEDDING_TIMEOUT")
                item.started = True
                self._start()
                io = asyncio.create_task(asyncio.to_thread(self._exchange, item))
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
