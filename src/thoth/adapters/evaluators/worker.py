"""Fixed bounded JSON interpreter. It does not load or execute candidate Python code."""

from __future__ import annotations

import json
import sys

from pydantic import Field, JsonValue

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.domain.base import DomainModel
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.evaluation_run import (
    BehaviorEvaluationProgram,
    CompiledEvaluationProgram,
    EvaluationInput,
    Expression,
)


def evaluate(
    node: Expression, payload: dict[str, JsonValue], memory: dict[str, JsonValue]
) -> JsonValue:
    if node.op == "literal":
        return node.value
    if node.op == "input":
        return payload.get(str(node.key))
    if node.op == "memory":
        return memory.get(str(node.key))
    if node.op == "if":
        condition = evaluate(node.args[0], payload, memory)
        if not isinstance(condition, bool):
            raise ValueError("EVALUATION_CONDITION_NOT_BOOLEAN")
        return evaluate(node.args[1 if condition else 2], payload, memory)
    left, right = (evaluate(child, payload, memory) for child in node.args)
    if node.op == "equal":
        return type(left) is type(right) and left == right
    if type(left) is not int or type(right) is not int:
        raise ValueError("EVALUATION_ARITHMETIC_NOT_INTEGER")
    result = left + right if node.op == "add" else left - right
    if abs(result) > 10**18:
        raise ValueError("EVALUATION_ARITHMETIC_LIMIT")
    return result


class ExecutionEnvelope(DomainModel):
    program: CompiledEvaluationProgram
    inputs: tuple[EvaluationInput, ...] = Field(min_length=1, max_length=64)
    initial_memory: dict[str, JsonValue]
    max_output_bytes: int = Field(ge=128, le=1048576)


def main() -> int:
    raw = sys.stdin.buffer.read(2_097_153)
    if len(raw) > 2_097_152:
        raise ValueError("EVALUATION_INPUT_LIMIT")
    envelope = ExecutionEnvelope.model_validate_json(raw)
    program, inputs = envelope.program, envelope.inputs
    memory = dict(envelope.initial_memory)
    outputs: list[dict[str, JsonValue]] = []
    encoded = b""
    components = default_behavior_components()
    for case in inputs:
        if isinstance(program, BehaviorEvaluationProgram):
            handler = components.resolve(
                BehaviorArtifactKind(program.policy.kind), program.policy.version
            )
            outputs.append(handler.evaluate(program.policy, case.payload))
        else:
            outputs.append(
                {key: evaluate(node, case.payload, memory) for key, node in program.outputs.items()}
            )
            memory.update(
                {
                    key: evaluate(node, case.payload, memory)
                    for key, node in program.memory_updates.items()
                }
            )
        encoded = json.dumps(
            {"outputs": outputs, "final_memory": memory}, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(encoded) > envelope.max_output_bytes:
            sys.stdout.buffer.write(b'{"error_code":"EVALUATION_OUTPUT_LIMIT"}')
            return 2
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
