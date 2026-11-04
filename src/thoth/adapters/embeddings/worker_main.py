"""Private line protocol for an application-selected local encoder factory."""

import contextlib
import importlib
import json
import os
import socket
import sys
from typing import cast

from thoth.domain.embedding_index import EmbeddingBudget, EmbeddingVector
from thoth.domain.model_asset import EMBEDDING_ERROR_CODES, EmbeddingAssetDescriptor
from thoth.domain.retrieval_embedding import DenseModelPin
from thoth.ports.embedding import LocalEmbeddingPort


def denied(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("EMBEDDING_NETWORK_DISABLED")


def main() -> None:
    budget = EmbeddingBudget.model_validate_json(sys.argv[2])
    if sys.platform != "win32":
        import resource

        resource.setrlimit(
            resource.RLIMIT_AS, (budget.commit_limit_bytes, budget.commit_limit_bytes)
        )
    socket.socket.connect = denied
    socket.create_connection = denied
    module, name = sys.argv[1].split(":")
    descriptor = (
        EmbeddingAssetDescriptor.model_validate_json(sys.argv[3]) if len(sys.argv) > 3 else None
    )
    try:
        with contextlib.redirect_stdout(sys.stderr):
            factory = getattr(importlib.import_module(module), name)
            encoder = cast(
                LocalEmbeddingPort, factory() if descriptor is None else factory(descriptor)
            )
    except Exception as exc:
        code = str(exc)
        code = code if code in EMBEDDING_ERROR_CODES else "EMBEDDING_LOAD_FAILED"
        sys.stdout.write(json.dumps({"kind": "error", "protocol": 2, "error": code}) + "\n")
        sys.stdout.flush()
        return
    if descriptor is not None:
        sys.stdout.write(json.dumps({"kind": "ready", "protocol": 2, "pid": os.getpid()}) + "\n")
        sys.stdout.flush()
    while True:
        result: dict[str, str | int]
        max_line = (
            6 * budget.max_input_bytes + 4096
        )  # JSON escaping can expand a control byte sixfold.
        line = sys.stdin.buffer.readline(max_line + 1)
        if not line:
            return
        if len(line) > max_line:
            raise RuntimeError("EMBEDDING_INPUT_TOO_LARGE")
        try:
            request = json.loads(line)
            if (
                set(request) != {"text", "role"}
                or not isinstance(request["text"], str)
                or request["role"] not in ("QUERY", "PASSAGE")
            ):
                raise ValueError("EMBEDDING_PROTOCOL_INVALID")
            if len(request["text"].encode()) > budget.max_input_bytes:
                raise ValueError("EMBEDDING_INPUT_TOO_LARGE")
            with contextlib.redirect_stdout(sys.stderr):
                vector = EmbeddingVector.from_values(
                    encoder.encode_one(
                        request["text"],
                        role=request["role"],
                        model=DenseModelPin() if descriptor is None else descriptor.pin,
                    )
                )
            result = {"data": vector.data.hex(), "sha256": vector.sha256}
        except Exception as exc:
            code = str(exc)
            result = {"error": code if code in EMBEDDING_ERROR_CODES else "EMBEDDING_FAILED"}
        if descriptor is not None:
            result.update(kind="error" if "error" in result else "vector", protocol=2)
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
