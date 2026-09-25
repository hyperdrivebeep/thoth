"""Read-only Codex account quota observation. Never creates models or resets limits."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from thoth.domain.account_usage import AccountQuotaSnapshot


class CodexAccountUsageAdapter:
    def __init__(self, executable: Path | None = None) -> None:
        self.executable = executable

    def refresh(self) -> AccountQuotaSnapshot:
        observed = datetime.now(UTC).isoformat()
        resolved = self.executable or shutil.which("codex")
        if resolved is None:
            return AccountQuotaSnapshot(
                state="UNSUPPORTED",
                observed_at=observed,
                reason="CLI_NOT_FOUND",
                provider="codex-oauth",
            )
        try:
            completed = subprocess.run(
                [str(resolved), "app-server", "--help"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return AccountQuotaSnapshot(
                state="UNSUPPORTED",
                observed_at=observed,
                reason="APP_SERVER_UNAVAILABLE",
                provider="codex-oauth",
            )
        help_text = (completed.stdout or "") + chr(10) + (completed.stderr or "")
        if completed.returncode != 0 or "app-server" not in help_text.lower():
            return AccountQuotaSnapshot(
                state="UNSUPPORTED",
                observed_at=observed,
                reason="APP_SERVER_UNSUPPORTED",
                provider="codex-oauth",
            )
        return AccountQuotaSnapshot(
            state="UNKNOWN",
            observed_at=observed,
            source="CODEX_APP_SERVER_RATE_LIMITS",
            reason="RATE_LIMITS_UNVERIFIED",
            provider="codex-oauth",
        )


def decode_rate_limit_payload(payload: object, observed_at: str) -> AccountQuotaSnapshot:
    if not isinstance(payload, dict):
        return AccountQuotaSnapshot(
            state="UNKNOWN", observed_at=observed_at, reason="PAYLOAD_UNAVAILABLE"
        )
    data = cast(dict[object, object], payload)
    windows = data.get("rateLimits")
    if not isinstance(windows, list) or not windows:
        windows = data.get("rate_limits")
    if not isinstance(windows, list) or not windows:
        return AccountQuotaSnapshot(
            state="UNKNOWN",
            observed_at=observed_at,
            source="CODEX_APP_SERVER_RATE_LIMITS",
            reason="WINDOWS_UNAVAILABLE",
        )
    first = cast(list[object], windows)[0]
    if not isinstance(first, dict):
        return AccountQuotaSnapshot(
            state="UNKNOWN",
            observed_at=observed_at,
            source="CODEX_APP_SERVER_RATE_LIMITS",
            reason="WINDOW_UNAVAILABLE",
        )
    window = cast(dict[object, object], first)
    used = window.get("usedPercent")
    if used is None:
        used = window.get("used_percent")
    remaining = None
    if isinstance(used, (int, float)) and not isinstance(used, bool) and 0 <= used <= 100:
        remaining = round(100 - float(used), 4)
    duration = window.get("windowDurationMins")
    if duration is None:
        duration = window.get("window_duration_mins")
    resets = window.get("resetsAt")
    if resets is None:
        resets = window.get("resets_at")
    return AccountQuotaSnapshot(
        state="OBSERVED" if remaining is not None else "UNKNOWN",
        remaining_percent=remaining,
        window_minutes=duration if isinstance(duration, int) and duration >= 1 else None,
        resets_at=str(resets) if isinstance(resets, str) and resets else None,
        observed_at=observed_at,
        source="CODEX_APP_SERVER_RATE_LIMITS",
        reason=None if remaining is not None else "USED_PERCENT_UNAVAILABLE",
        provider="codex-oauth",
    )
