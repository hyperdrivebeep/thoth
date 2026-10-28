"""Fresh subprocess/workspace for a trusted declarative interpreter, without parent secrets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from time import perf_counter_ns
from typing import Literal, cast

from thoth.adapters.evaluators.process_tree import ProcessTree
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evaluation_run import (
    BehaviorEvaluationProgram,
    CompiledEvaluationProgram,
    EvaluationExecutionReceipt,
    EvaluationInput,
    EvaluationRunError,
    EvaluationRunSpec,
    sealed_payload,
)
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

# How long the worker's cleanup (end the process, wait for it, close and empty its pipes) may take
# once the run is over. It is milliseconds in practice: the worker is a fixed program with no
# children. 5 s is far above a loaded machine's worst case yet well under the 30 s default run
# limit, and under the workspace removal's own retries that follow. Past it the whole process tree
# is terminated, the workspace folder is left, and the receipt says the cleanup did not finish.
CLEANUP_SECONDS = 5.0
# After the tree is terminated the cleanup gets this long to finish before it is given up.
FORCE_GRACE_SECONDS = 1.0

_UNFINISHED_CLEANUPS: set[asyncio.Task[None]] = set()


class IsolatedProgramExecutor:
    executor_id = "PURE_TRANSFORM_SUBPROCESS_V1"
    version = "1.0.0"
    program_schema = "PURE_TRANSFORM_V1"

    def __init__(self, *, objects: ObjectStorePort, clock: ClockPort, ids: IdGeneratorPort) -> None:
        self._objects = objects
        self._clock = clock
        self._ids = ids

    def worker_argv(self) -> tuple[str, ...]:
        """The command that runs the worker; the one place it is named."""
        return (sys.executable, "-m", "thoth.adapters.evaluators.worker")

    async def execute(
        self,
        spec: EvaluationRunSpec,
        arm: Literal["BASELINE", "CANDIDATE"],
        program: CompiledEvaluationProgram,
        inputs: tuple[EvaluationInput, ...],
        initial_memory: dict[str, object],
    ) -> EvaluationExecutionReceipt:
        if program.schema_version != self.program_schema:
            raise EvaluationRunError("EVALUATION_EXECUTOR_PROGRAM_MISMATCH")
        if (
            isinstance(program, BehaviorEvaluationProgram)
            and program.policy.kind != spec.component.value
        ):
            raise EvaluationRunError("EVALUATION_PROGRAM_COMPONENT_MISMATCH")
        artifact_digest = spec.baseline_digest if arm == "BASELINE" else spec.candidate_digest
        public_inputs = [case.model_dump(mode="python") for case in inputs]
        input_digest = domain_digest(
            "EVALUATION_PUBLIC_INPUTS", "1.0.0", canonical_payload({"cases": public_inputs})
        )
        memory_digest = domain_digest(
            "EVALUATION_MEMORY", "1.0.0", canonical_payload(initial_memory)
        )
        if input_digest != spec.public_input_digest or memory_digest != spec.initial_memory_digest:
            raise EvaluationRunError("EVALUATION_EXECUTOR_INPUT_MISMATCH")
        envelope = canonical_payload(
            {
                "program": program.model_dump(mode="python"),
                "inputs": public_inputs,
                "initial_memory": initial_memory,
                "max_output_bytes": spec.max_output_bytes,
            }
        )
        if len(envelope) > 2_097_152:
            raise EvaluationRunError("EVALUATION_INPUT_LIMIT")
        timeout = min(spec.timeout_seconds, (spec.deadline_at - self._clock.now()).total_seconds())
        if timeout <= 0:
            raise EvaluationRunError("EVALUATION_DEADLINE_EXPIRED")
        started = self._clock.now()
        stopwatch = perf_counter_ns()
        execution_id = self._ids.new("evaluation-execution")
        workspace_id = self._ids.new("evaluation-workspace")
        state: Literal["SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED", "OUTPUT_LIMIT"] = "FAILED"
        output = b'{"error_code":"EVALUATION_EXECUTION_FAILED"}'
        # delete=False: the folder is removed only by the explicit cleanup below, so a run whose
        # cleanup did not finish leaves it in place instead of the finalizer removing it later.
        directory = tempfile.TemporaryDirectory(prefix="thoth-evaluation-", delete=False)
        workspace = directory.name
        tree: ProcessTree | None = None
        overran = False
        try:
            environment = {
                "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
                "TEMP": workspace,
                "TMP": workspace,
                "PYTHONIOENCODING": "utf-8",
            }
            if "SYSTEMROOT" in os.environ:
                environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
            creation = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *self.worker_argv(),
                    cwd=workspace,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=0x08000000 if sys.platform == "win32" else 0,
                    start_new_session=sys.platform != "win32",
                )
            )
            process = None
            try:
                async with asyncio.timeout(timeout):
                    process = await asyncio.shield(creation)
                    tree = ProcessTree(process.pid)
                    assert process.stdin is not None
                    process.stdin.write(envelope)
                    await process.stdin.drain()
                    process.stdin.close()
                    output = await self._bounded_output(process, spec.max_output_bytes)
                    state = "SUCCEEDED" if process.returncode == 0 else "FAILED"
                    if process.returncode == 2:
                        state = "OUTPUT_LIMIT"
            except TimeoutError:
                state, output = "TIMEOUT", b'{"error_code":"EVALUATION_TIMEOUT"}'
            except asyncio.CancelledError:
                state, output = "CANCELLED", b'{"error_code":"EVALUATION_CANCELLED"}'
            except EvaluationRunError:
                state, output = "OUTPUT_LIMIT", b'{"error_code":"EVALUATION_OUTPUT_LIMIT"}'
            finally:
                cleanup = asyncio.create_task(self._cleanup_process(creation))
                overran, cancelled, tree = await self._finish_cleanup(cleanup, creation, tree)
                if cancelled:
                    state, output = "CANCELLED", b'{"error_code":"EVALUATION_CANCELLED"}'
        finally:
            if overran:
                # The workspace is left for inspection, never removed by a run that did not finish.
                state, output = self._incomplete(state, output, Path(workspace).name)
            else:
                cleanup_workspace = asyncio.create_task(self._cleanup_workspace(directory))
                while True:
                    try:
                        await asyncio.shield(cleanup_workspace)
                        break
                    except asyncio.CancelledError:
                        state, output = "CANCELLED", b'{"error_code":"EVALUATION_CANCELLED"}'
            if tree is not None:
                tree.close()
        elapsed = perf_counter_ns() - stopwatch
        output_digest = hashlib.sha256(output).hexdigest()
        self._objects.put(output, output_digest, operation_id=execution_id)
        final_memory = None
        if state == "SUCCEEDED":
            parsed = json.loads(output)
            final_memory = domain_digest(
                "EVALUATION_MEMORY",
                "1.0.0",
                canonical_payload(cast(dict[str, object], parsed["final_memory"])),
            )
        return EvaluationExecutionReceipt.model_validate(
            sealed_payload(
                "EVALUATION_EXECUTION_RECEIPT",
                "receipt_digest",
                {
                    "execution_id": execution_id,
                    "pair_id": spec.pair_id,
                    "project_id": spec.project_id,
                    "arm": arm,
                    "artifact_digest": artifact_digest,
                    "input_digest": input_digest,
                    "initial_memory_digest": memory_digest,
                    "final_memory_digest": final_memory,
                    "output_blob_digest": output_digest,
                    "workspace_id": workspace_id,
                    "executor_id": self.executor_id,
                    "executor_version": self.version,
                    "isolation": "TRUSTED_DECLARATIVE_SUBPROCESS",
                    "state": state,
                    "case_count": len(inputs) if state == "SUCCEEDED" else 0,
                    "elapsed_ns": elapsed,
                    "billed_cost_microunits": 0,
                    "cost_basis": "NO_BILLABLE_PROVIDER_CALLS",
                    "started_at": started,
                    "completed_at": self._clock.now(),
                },
            )
        )

    @staticmethod
    def _incomplete(
        state: Literal["SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED", "OUTPUT_LIMIT"],
        output: bytes,
        workspace_name: str,
    ) -> tuple[Literal["SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED", "OUTPUT_LIMIT"], bytes]:
        """The outcome of a run whose cleanup went past its bound.

        A cancel stays the outcome (the caller cancelled it). Any other outcome becomes FAILED, so
        a result from a run that left residue is never scored; the outcome it had is kept in the
        output, with the name of the workspace folder that was left.
        """
        recorded: dict[str, object] = {"cleanup": "INCOMPLETE", "workspace": workspace_name}
        if state == "CANCELLED":
            recorded["error_code"] = "EVALUATION_CANCELLED"
            return "CANCELLED", json.dumps(recorded, sort_keys=True, separators=(",", ":")).encode()
        recorded["error_code"] = "EVALUATION_CLEANUP_INCOMPLETE"
        recorded["outcome"] = state
        return "FAILED", json.dumps(recorded, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    async def _finish_cleanup(
        cleanup: asyncio.Task[None],
        creation: asyncio.Task[asyncio.subprocess.Process],
        tree: ProcessTree | None,
    ) -> tuple[bool, bool, ProcessTree | None]:
        """Wait for the cleanup, protected from cancellation, for at most CLEANUP_SECONDS.

        Returns (overran, cancelled, tree). A cancel that arrives is not lost: `cancelled` says so
        and the caller makes it the outcome. Past the bound the process tree is terminated and the
        cleanup gets FORCE_GRACE_SECONDS more; a cleanup that is still not done is cancelled.
        """
        loop = asyncio.get_running_loop()
        end = loop.time() + CLEANUP_SECONDS
        overran = cancelled = False
        while True:
            remaining = end - loop.time()
            if remaining <= 0:
                if overran:
                    break
                overran = True
                if (
                    tree is None
                    and creation.done()
                    and not creation.cancelled()
                    and creation.exception() is None
                ):
                    tree = ProcessTree(creation.result().pid)
                if tree is not None:
                    tree.terminate()
                end = loop.time() + FORCE_GRACE_SECONDS
                continue
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), remaining)
                return overran, cancelled, tree
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                # Repeated cancellation cannot abandon a child, but it cannot extend the bound.
                cancelled = True
        if not cleanup.done():
            cleanup.cancel()
            _UNFINISHED_CLEANUPS.add(cleanup)
            cleanup.add_done_callback(_UNFINISHED_CLEANUPS.discard)
        elif not cleanup.cancelled():
            cleanup.exception()
        return True, cancelled, tree

    @staticmethod
    async def _cleanup_workspace(directory: tempfile.TemporaryDirectory[str]) -> None:
        target = Path(directory.name).resolve()
        if target.parent != Path(tempfile.gettempdir()).resolve() or not target.name.startswith(
            "thoth-evaluation-"
        ):
            raise EvaluationRunError("EVALUATION_WORKSPACE_SCOPE_INVALID")
        for attempt in range(40):
            try:
                directory.cleanup()
                return
            except PermissionError as exc:
                if attempt == 39:
                    raise EvaluationRunError("EVALUATION_WORKSPACE_CLEANUP_FAILED") from exc
                await asyncio.sleep(0.05)

    @staticmethod
    async def _cleanup_process(creation: asyncio.Task[asyncio.subprocess.Process]) -> None:
        process = await creation
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()
        if process.stdin is not None:
            process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        if process.stdout is not None:
            while await process.stdout.read(65536):
                pass

    @staticmethod
    async def _bounded_output(process: asyncio.subprocess.Process, limit: int) -> bytes:
        assert process.stdout is not None
        chunks: list[bytes] = []
        size = 0
        while chunk := await process.stdout.read(65536):
            size += len(chunk)
            if size > limit:
                raise EvaluationRunError("EVALUATION_OUTPUT_LIMIT")
            chunks.append(chunk)
        await process.wait()
        return b"".join(chunks)


class BehaviorProgramExecutor(IsolatedProgramExecutor):
    executor_id = "BEHAVIOR_COMPONENT_SUBPROCESS_V1"
    program_schema = "BEHAVIOR_COMPONENT_V1"
