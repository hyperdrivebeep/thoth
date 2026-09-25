import subprocess
from pathlib import Path

import pytest

from thoth.adapters.models.codex_account_usage import (
    CodexAccountUsageAdapter,
    decode_rate_limit_payload,
)


def test_rate_limit_window_is_observation_not_inferred_tokens() -> None:
    snapshot = decode_rate_limit_payload(
        {"rateLimits": [{"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": "later"}]},
        "2026-09-17T00:00:00+00:00",
    )
    assert snapshot.state == "OBSERVED"
    assert snapshot.remaining_percent == 60
    assert snapshot.window_minutes == 10080
    assert snapshot.source == "CODEX_APP_SERVER_RATE_LIMITS"


def test_invalid_used_percent_preserves_unknown_quota() -> None:
    snapshot = decode_rate_limit_payload(
        {"rate_limits": [{"used_percent": True, "window_duration_mins": 0}]},
        "2026-09-17T00:00:00+00:00",
    )
    assert snapshot.state == "UNKNOWN"
    assert snapshot.remaining_percent is None
    assert snapshot.window_minutes is None
    assert snapshot.reason == "USED_PERCENT_UNAVAILABLE"


def test_codex_account_probe_uses_fake_subprocess_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="app-server", stderr="")

    monkeypatch.setattr("thoth.adapters.models.codex_account_usage.subprocess.run", fake_run)
    snapshot = CodexAccountUsageAdapter(tmp_path / "codex.exe").refresh()
    assert called == [[str(tmp_path / "codex.exe"), "app-server", "--help"]]
    assert snapshot.state == "UNKNOWN"
    assert snapshot.reason == "RATE_LIMITS_UNVERIFIED"
