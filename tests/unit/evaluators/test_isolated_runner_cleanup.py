"""A worker that will not finish cleaning up is ended after a bound, and the result says so.

The evaluator's cleanup used to wait without limit, shielded from cancellation: a worker that left a
grandchild holding its stdout kept `execute` from ever returning. Now the cleanup has a bound; past
it the whole process tree is terminated, the workspace folder is left where it is, the receipt
records that the cleanup did not finish, and a cancel that arrived meanwhile is still the outcome.
"""

import asyncio
import ctypes
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest

from thoth.adapters.evaluators import isolated_runner
from thoth.adapters.evaluators.isolated_runner import IsolatedProgramExecutor
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import (
    EvaluationExecutionReceipt,
    EvaluationProgram,
    EvaluationRunSpec,
)
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

SAFETY = 30.0  # protects the test run only; the code under test must finish well inside it
GRANDCHILD = "import time; time.sleep(300)"


@pytest.fixture(autouse=True)
def short_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite quick: the bound is a module constant, so these runs use a shorter one."""
    monkeypatch.setattr(isolated_runner, "CLEANUP_SECONDS", 1.0)


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, kind: str) -> str:
        self.count += 1
        return f"{kind}:{self.count}"


class Objects:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, data: bytes, digest: str, *, operation_id: str) -> None:
        del operation_id
        self.blobs[digest] = data


def worker_code(marker: Path, *, finish: bool, spawn: bool) -> str:
    """A worker that may start a grandchild which inherits its stdout, then waits or finishes."""
    start = (
        f"p = subprocess.Popen([sys.executable, '-c', {GRANDCHILD!r}]); "
        f"open({str(marker)!r}, 'w').write(str(p.pid)); "
        if spawn
        else ""
    )
    end = (
        'sys.stdout.write(\'{"outputs":[],"final_memory":{}}\'); sys.stdout.flush()'
        if finish
        else "time.sleep(300)"
    )
    return f"import subprocess, sys, time; sys.stdin.buffer.read(); {start}{end}"


class Executor(IsolatedProgramExecutor):
    def __init__(self, code: str) -> None:
        self.objects = Objects()
        super().__init__(
            objects=cast(ObjectStorePort, self.objects),
            clock=cast(ClockPort, Clock()),
            ids=cast(IdGeneratorPort, Ids()),
        )
        self.code = code

    def worker_argv(self) -> tuple[str, ...]:
        return (sys.executable, "-c", self.code)


def spec_and_program() -> tuple[EvaluationRunSpec, EvaluationProgram]:
    program = EvaluationProgram.model_validate({"outputs": {"x": {"op": "literal", "value": 1}}})
    digest = domain_digest("EVALUATION_PUBLIC_INPUTS", "1.0.0", canonical_payload({"cases": []}))
    memory = domain_digest("EVALUATION_MEMORY", "1.0.0", canonical_payload({}))
    now = datetime.now(UTC)
    spec = EvaluationRunSpec.model_construct(
        pair_id="pair",
        project_id="project",
        baseline_digest="a" * 64,
        candidate_digest="b" * 64,
        public_input_digest=digest,
        initial_memory_digest=memory,
        max_output_bytes=100_000,
        timeout_seconds=1,
        deadline_at=now + timedelta(seconds=60),
    )
    return spec, program


async def run(executor: Executor) -> EvaluationExecutionReceipt:
    spec, program = spec_and_program()
    return await executor.execute(spec, "BASELINE", program, (), {})


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def blob(executor: Executor, receipt: EvaluationExecutionReceipt) -> dict[str, object]:
    return cast(dict[str, object], json.loads(executor.objects.blobs[receipt.output_blob_digest]))


def workspace_of(recorded: dict[str, object]) -> Path:
    path = Path(tempfile.gettempdir()) / str(recorded["workspace"])
    assert path.name.startswith("thoth-evaluation-") and path.parent == Path(tempfile.gettempdir())
    return path


def left_over(marker: Path) -> int:
    return int(marker.read_text())


async def test_a_grandchild_holding_stdout_does_not_keep_the_executor_from_returning(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "grandchild.pid"
    executor = Executor(worker_code(marker, finish=False, spawn=True))
    started = time.monotonic()
    receipt = await asyncio.wait_for(run(executor), SAFETY)
    elapsed = time.monotonic() - started
    pid = left_over(marker)
    recorded = blob(executor, receipt)
    workspace = workspace_of(recorded)
    try:
        assert elapsed < isolated_runner.CLEANUP_SECONDS + 10
        assert receipt.state == "FAILED"
        assert recorded["error_code"] == "EVALUATION_CLEANUP_INCOMPLETE"
        assert recorded["cleanup"] == "INCOMPLETE"
        # No process of the tree is left.
        deadline = time.monotonic() + 10
        while alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not alive(pid)
        # The workspace folder is kept for inspection.
        assert workspace.is_dir()
    finally:
        if alive(pid):
            os.kill(pid, 9)
        shutil.rmtree(workspace, ignore_errors=True)


async def test_a_cleanup_that_never_ends_is_given_up_and_the_worker_is_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "grandchild.pid"
    executor = Executor(worker_code(marker, finish=False, spawn=True))

    async def never(*_args: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(IsolatedProgramExecutor, "_cleanup_process", staticmethod(never))
    receipt = await asyncio.wait_for(run(executor), SAFETY)
    pid = left_over(marker)
    recorded = blob(executor, receipt)
    try:
        assert receipt.state == "FAILED" and recorded["cleanup"] == "INCOMPLETE"
        deadline = time.monotonic() + 10
        while alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not alive(pid)
        assert workspace_of(recorded).is_dir()
    finally:
        if alive(pid):
            os.kill(pid, 9)
        shutil.rmtree(workspace_of(recorded), ignore_errors=True)


async def test_a_cancel_while_the_run_waits_is_the_outcome_and_the_cleanup_is_still_bounded(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "grandchild.pid"
    executor = Executor(worker_code(marker, finish=False, spawn=True))
    task = asyncio.create_task(run(executor))
    # The worker has started its grandchild; the run is now waiting for it (then for its timeout).
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    task.cancel()
    receipt = await asyncio.wait_for(task, SAFETY)
    pid = left_over(marker)
    recorded = blob(executor, receipt)
    try:
        # The cancel is the outcome, as it always was, and the unfinished cleanup is recorded too.
        assert receipt.state == "CANCELLED"
        assert (
            recorded["error_code"] == "EVALUATION_CANCELLED" and recorded["cleanup"] == "INCOMPLETE"
        )
        deadline = time.monotonic() + 10
        while alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not alive(pid)
    finally:
        if alive(pid):
            os.kill(pid, 9)
        if "workspace" in recorded:
            shutil.rmtree(workspace_of(recorded), ignore_errors=True)


async def test_a_worker_that_finishes_is_unchanged_and_its_workspace_is_removed(
    tmp_path: Path,
) -> None:
    executor = Executor(worker_code(tmp_path / "unused.pid", finish=True, spawn=False))
    before = set(Path(tempfile.gettempdir()).glob("thoth-evaluation-*"))
    receipt = await asyncio.wait_for(run(executor), SAFETY)
    state: Literal["SUCCEEDED"] = "SUCCEEDED"
    assert receipt.state == state
    assert blob(executor, receipt) == {"outputs": [], "final_memory": {}}
    assert set(Path(tempfile.gettempdir()).glob("thoth-evaluation-*")) == before


async def test_a_cancel_that_arrives_during_the_cleanup_is_not_lost_and_the_cleanup_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "grandchild.pid"
    executor = Executor(worker_code(marker, finish=False, spawn=True))
    started = asyncio.Event()
    original = cast(
        Callable[[asyncio.Task[asyncio.subprocess.Process]], Awaitable[None]],
        vars(IsolatedProgramExecutor)["_cleanup_process"].__func__,
    )

    async def noticed(creation: asyncio.Task[asyncio.subprocess.Process]) -> None:
        started.set()
        await original(creation)

    monkeypatch.setattr(IsolatedProgramExecutor, "_cleanup_process", staticmethod(noticed))
    task = asyncio.create_task(run(executor))
    # The run has timed out on its own (the worker's grandchild holds the pipe) and is now cleaning
    # up; this is the cancel that must not be dropped.
    await asyncio.wait_for(started.wait(), SAFETY)
    task.cancel()
    begun = time.monotonic()
    receipt = await asyncio.wait_for(task, SAFETY)
    pid = left_over(marker)
    recorded = blob(executor, receipt)
    try:
        assert time.monotonic() - begun < isolated_runner.CLEANUP_SECONDS + 5
        assert receipt.state == "CANCELLED"
        assert recorded["error_code"] == "EVALUATION_CANCELLED"
        assert recorded["cleanup"] == "INCOMPLETE"
        deadline = time.monotonic() + 10
        while alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not alive(pid)
    finally:
        if alive(pid):
            os.kill(pid, 9)
        shutil.rmtree(workspace_of(recorded), ignore_errors=True)
