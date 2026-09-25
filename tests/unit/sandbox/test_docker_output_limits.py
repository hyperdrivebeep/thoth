from __future__ import annotations

import sys
from pathlib import Path

from thoth.adapters.sandbox import DockerSandboxAdapter, GVisorSandboxAdapter


def test_docker_attach_reader_bounds_stdout_and_stderr_before_process_completion(
    tmp_path: Path,
) -> None:
    adapter = DockerSandboxAdapter(tmp_path, docker_binary=sys.executable)
    stdout_result, stdout_truncated, stderr_truncated = adapter._docker_call_bounded(  # pyright: ignore[reportPrivateUsage]
        "-c",
        "import sys; sys.stdout.write('x'*1000000)",
        timeout=5,
        stdout_limit=64,
        stderr_limit=32,
    )
    assert stdout_truncated is True
    assert stderr_truncated is False
    assert len(stdout_result.stdout.encode()) <= 64
    assert stdout_result.stderr == ""

    stderr_result, stdout_truncated, stderr_truncated = adapter._docker_call_bounded(  # pyright: ignore[reportPrivateUsage]
        "-c",
        "import sys; sys.stderr.write('y'*1000000)",
        timeout=5,
        stdout_limit=64,
        stderr_limit=32,
    )
    assert stdout_truncated is False
    assert stderr_truncated is True
    assert stderr_result.stdout == ""
    assert len(stderr_result.stderr.encode()) <= 32


def test_gvisor_inherits_bounded_attach_reader_and_small_output_is_unchanged(
    tmp_path: Path,
) -> None:
    adapter = GVisorSandboxAdapter(tmp_path, docker_binary=sys.executable)
    completed, stdout_truncated, stderr_truncated = adapter._docker_call_bounded(  # pyright: ignore[reportPrivateUsage]
        "-c",
        "import sys; sys.stdout.write('ok'); sys.stderr.write('warn')",
        timeout=5,
        stdout_limit=64,
        stderr_limit=32,
    )
    assert completed.stdout == "ok"
    assert completed.stderr == "warn"
    assert stdout_truncated is False
    assert stderr_truncated is False


def test_invalid_utf8_at_exact_raw_byte_limit_is_not_misclassified(
    tmp_path: Path,
) -> None:
    adapter = DockerSandboxAdapter(tmp_path, docker_binary=sys.executable)
    completed, stdout_truncated, stderr_truncated = adapter._docker_call_bounded(  # pyright: ignore[reportPrivateUsage]
        "-c",
        "import os; os.write(1, b'\\xff'*4)",
        timeout=5,
        stdout_limit=4,
        stderr_limit=4,
    )
    assert completed.stdout
    assert stdout_truncated is False
    assert stderr_truncated is False
