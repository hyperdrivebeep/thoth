from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import check_post_d4_baseline

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "config/post-d4-baseline.json"
NOTIFICATION_HISTORY = ROOT / "config/post-d4-notifications-historical.json"


def _historical_names() -> tuple[str, ...]:
    snapshot = json.loads(NOTIFICATION_HISTORY.read_text(encoding="utf-8"))
    names = tuple(snapshot["names"])
    assert len(names) == len(set(names)) == 219
    assert list(names) == sorted(names)
    assert hashlib.sha256(
        json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest() == "03b79293bc2513d9f9f234ec0d50ec2372dcc4d8bd58838d3891afdcbe1aecca"
    return names


def test_post_d4_baseline_is_exact_and_self_validating() -> None:
    before = BASELINE.read_bytes()
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert payload["record_type"] == "POST_D4_BASELINE"
    assert payload["architecture"]["exception_count"] == 0
    assert payload["architecture"]["acceptance_blocker_count"] == 0
    assert payload["migration"] == {
        "head": "9d12a7c5e3b8",
        "migration_count": 22,
        "runtime_create_all_exception_count": 0,
    }
    assert payload["protocol"]["runtime_public_method_count"] == 309
    assert payload["protocol"]["canonical_callable_count"] == 295
    assert payload["protocol"]["implemented_notification_count"] == 219
    assert set(payload["acceptance_receipts"]) == {f"A{index:02d}" for index in range(1, 14)}
    assert set(payload["projectpack_digests"]) == {
        "6g-sandbox-hero",
        "public-demo-membrane",
        "sunrise-secondary",
        "l3pilot-regression",
        "opendreamkit-hidden-holdout",
    }
    assert all(value == "NOT_RUN" for value in payload["external_gates"].values())

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_post_d4_baseline.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    validation = json.loads(result.stdout)
    assert validation["verdict"] == "PASS"
    assert validation["baseline_receipt_digest"] == payload["baseline_receipt_digest"]
    assert any(
        "implemented notifications advanced" in item for item in validation["additive_drift"]
    )
    assert BASELINE.read_bytes() == before


@pytest.mark.parametrize("count,exit_code", [(309, 0), (314, 0), (308, 1)])
def test_historical_protocol_count_accepts_additions_but_rejects_loss(
    count: int, exit_code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = BASELINE.read_bytes()
    monkeypatch.setattr(
        check_post_d4_baseline, "PUBLIC_METHODS", tuple(f"method:{i}" for i in range(count))
    )
    assert check_post_d4_baseline.main() == exit_code
    result = json.loads(capsys.readouterr().out)
    assert bool(result["errors"]) is (count < 309)
    assert any("public methods advanced" in item for item in result["additive_drift"]) is (
        count > 309
    )
    assert BASELINE.read_bytes() == before


def test_historical_notification_names_allow_only_additions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = BASELINE.read_bytes()
    historical = frozenset(_historical_names())
    actual = check_post_d4_baseline.IMPLEMENTED_NOTIFICATIONS
    assert historical <= actual
    assert {"project/archived", "revision/recomputeRequested"} <= actual - historical
    monkeypatch.setattr(
        check_post_d4_baseline,
        "IMPLEMENTED_NOTIFICATIONS",
        historical | {"fixture/new-notification"},
    )
    assert check_post_d4_baseline.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["errors"] == []
    assert any("implemented notifications advanced" in item for item in result["additive_drift"])
    assert BASELINE.read_bytes() == before


@pytest.mark.parametrize("replacement", [False, True])
def test_historical_notification_loss_fails_even_when_count_is_replaced(
    replacement: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = BASELINE.read_bytes()
    names = frozenset(_historical_names())
    missing = min(names)
    current = names - {missing}
    if replacement:
        current |= {"fixture/replacement-notification"}
        assert len(current) == len(names)
    monkeypatch.setattr(check_post_d4_baseline, "IMPLEMENTED_NOTIFICATIONS", current)
    assert check_post_d4_baseline.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert any(
        "historical implemented notification names lost" in item for item in result["errors"]
    )
    assert BASELINE.read_bytes() == before


def test_historical_notification_snapshot_cannot_be_self_resealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = BASELINE.read_bytes()
    snapshot = json.loads(NOTIFICATION_HISTORY.read_text(encoding="utf-8"))
    names = list(_historical_names())
    names[0] = "fixture/replacement-notification"
    names.sort()
    snapshot["names"] = names
    snapshot["sorted_names_sha256"] = hashlib.sha256(
        json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    altered = tmp_path / "altered-notifications.json"
    altered.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(check_post_d4_baseline, "NOTIFICATION_HISTORY", altered)
    assert check_post_d4_baseline.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert any("historical notification snapshot mismatch" in item for item in result["errors"])
    assert BASELINE.read_bytes() == before
