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


class IsolatedProgramExecutor:
    executor_id = "PURE_TRANSFORM_SUBPROCESS_V1"
    version = "1.0.0"
    program_schema = "PURE_TRANSFORM_V1"

    def __init__(self, *, objects: ObjectStorePort, clock: ClockPort, ids: IdGeneratorPort) -> None:
        self._objects = objects
        self._clock = clock
        self._ids = ids

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
        directory = tempfile.TemporaryDirectory(prefix="thoth-evaluation-")
        workspace = directory.name
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
                    sys.executable,
                    "-m",
                    "thoth.adapters.evaluators.worker",
                    cwd=workspace,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=0x08000000 if sys.platform == "win32" else 0,
                )
            )
            process = None
            try:
                async with asyncio.timeout(timeout):
                    process = await asyncio.shield(creation)
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
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        # Repeated cancellation cannot abandon a child or its workspace.
                        state, output = "CANCELLED", b'{"error_code":"EVALUATION_CANCELLED"}'
        finally:
            cleanup_workspace = asyncio.create_task(self._cleanup_workspace(directory))
            while True:
                try:
                    await asyncio.shield(cleanup_workspace)
                    break
                except asyncio.CancelledError:
                    state, output = "CANCELLED", b'{"error_code":"EVALUATION_CANCELLED"}'
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
