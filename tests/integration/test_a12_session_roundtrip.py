from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import request, value

from thoth.apps.runtime import create_runtime


@pytest.mark.asyncio
async def test_assigned_session_records_timer_tool_transitions_and_manual_reentry(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "field-roundtrip", measurement_secret=b"field-test-secret")
    project_id = "project:a12:roundtrip"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "a12-roundtrip-project",
                    {
                        "project_id": project_id,
                        "name": "A12 session roundtrip",
                        "cutoff_at": "2026-09-02T00:00:00Z",
                    },
                )
            )
        )
        protocol = cast(
            dict[str, JsonValue],
            value(
                await runtime.bus.dispatch(
                    request(
                        "field/protocol/seal",
                        "a12-roundtrip-protocol",
                        {
                            "project_id": project_id,
                            "protocol_version": "field-v1",
                            "case_digests": {"case:1": "1" * 64},
                            "arms": ["A", "B", "C"],
                            "sequence_matrix": ["ABC", "BCA", "CAB", "ACB", "CBA", "BAC"],
                            "baseline_toolchain": ["manual", "gpt", "thoth"],
                            "thresholds": {"time_reduction_bps": 2000},
                            "hard_zero_metrics": ["unauthorized_r3"],
                        },
                    )
                )
            )["protocol"],
        )
        session = cast(
            dict[str, JsonValue],
            value(
                await runtime.bus.dispatch(
                    request(
                        "field/session/start",
                        "a12-roundtrip-start",
                        {
                            "project_id": project_id,
                            "protocol_digest": protocol["protocol_digest"],
                            "reviewer_external_ref": "local-fixture-reviewer",
                            "case_id": "case:1",
                            "arm": "C",
                            "sequence_position": 1,
                        },
                    )
                )
            )["session"],
        )
        session_id = str(session["session_id"])
        sensitive_value = await runtime.bus.dispatch(
            request(
                "field/event/record",
                "a12-sensitive-value",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "event_type": "SOURCE_OPEN",
                    "metric_delta": 1,
                    "metadata": {"tool_code": "contact person@example.com"},
                },
            )
        )
        assert sensitive_value.error is not None
        assert "privacy-sensitive" in sensitive_value.error.message
        numeric_identity = await runtime.bus.dispatch(
            request(
                "field/event/record",
                "a12-numeric-identity",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "event_type": "SOURCE_OPEN",
                    "metric_delta": 1,
                    "metadata": {"tool_code": 1012345678},
                },
            )
        )
        assert numeric_identity.error is not None
        assert "bounded codes" in numeric_identity.error.message
        for ordinal, event_type in enumerate(("APP_SWITCH", "COPY_RETYPE", "MANUAL_MAPPING")):
            value(
                await runtime.bus.dispatch(
                    request(
                        "field/event/record",
                        f"a12-event-{ordinal}",
                        {
                            "project_id": project_id,
                            "session_id": session_id,
                            "event_type": event_type,
                            "metric_delta": 1,
                            "metadata": {"tool_code": f"tool-{ordinal}"},
                        },
                    )
                )
            )
        ended = value(
            await runtime.bus.dispatch(
                request(
                    "field/session/end",
                    "a12-roundtrip-end",
                    {"project_id": project_id, "session_id": session_id, "timeout": False},
                )
            )
        )
        metrics = cast(dict[str, JsonValue], ended["metrics"])
        assert cast(int, metrics["active_milliseconds"]) >= 0
        assert metrics["app_switch_count"] == 1
        assert metrics["manual_reentry_count"] == 2
        assert metrics["timeout"] is False
    finally:
        runtime.close()


def test_session_runner_cli_replays_preregistered_event_taxonomy(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    assignment = tmp_path / "assignment.json"
    events = tmp_path / "events.jsonl"
    output = tmp_path / "session-summary.json"
    assignment.write_text(json.dumps({"assignment_digest": "a" * 64}), encoding="utf-8")
    events.write_text(
        "\n".join(
            json.dumps({"event_type": item})
            for item in ("SESSION_START", "APP_SWITCH", "COPY_RETYPE", "SESSION_END")
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "field-validation" / "session-runner.py"),
            "--assignment",
            str(assignment),
            "--events",
            str(events),
            "--started-at",
            "2026-09-02T00:00:00Z",
            "--ended-at",
            "2026-09-02T00:05:00Z",
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["summary"]["active_milliseconds"] == 300_000
    assert summary["summary"]["manual_reentry_count"] == 1
    assert summary["d6_claimed"] is False
