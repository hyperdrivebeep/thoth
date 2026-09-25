from __future__ import annotations

from pathlib import Path

from scripts.check_truth_drift import truth_drift_errors

ROOT = Path(__file__).resolve().parents[2]


def test_now_does_not_reopen_closed_ocp_work() -> None:
    now = (ROOT / "PROJECT_WIKI/NOW.md").read_text(encoding="utf-8")
    stale = (
        "OCP-CONNECTOR-001`, `OCP-SANDBOX-001`, `HERO-TRACE-001`로 OPEN",
        "최초 workspace→project 자동 binding은 OPEN",
    )
    assert all(value not in now for value in stale)


def test_ocp_projection_names_all_enforced_rules() -> None:
    page = (ROOT / "PROJECT_WIKI/30_ARCHITECTURE/current-ocp-gaps.md").read_text(
        encoding="utf-8"
    )
    required = (
        "OCP-REGISTRY-001",
        "OCP-FACTORY-001",
        "OCP-CORE-CLOSED-001",
        "OCP-DOC-DRIFT-001",
    )
    assert all(value in page for value in required)


def test_truth_drift_checker_rejects_reopened_ocp_claim(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "PROJECT_WIKI/30_ARCHITECTURE").mkdir(parents=True)
    (tmp_path / "PROJECT_WIKI/50_SEED_ROADMAP").mkdir(parents=True)
    (tmp_path / "config/architecture-conformance.json").write_text(
        '{"extension_points": ['
        '{"name":"PARSER","status":"IMPLEMENTED"},'
        '{"name":"CONNECTOR","status":"IMPLEMENTED"},'
        '{"name":"SANDBOX","status":"IMPLEMENTED"}]}'
    )
    (tmp_path / "PROJECT_WIKI/NOW.md").write_text(
        "OCP-CONNECTOR-001`, `OCP-SANDBOX-001`, `HERO-TRACE-001`로 OPEN",
        encoding="utf-8",
    )
    (tmp_path / "PROJECT_WIKI/30_ARCHITECTURE/current-ocp-gaps.md").write_text(
        "OCP-REGISTRY-001 OCP-FACTORY-001 OCP-CORE-CLOSED-001 OCP-DOC-DRIFT-001",
        encoding="utf-8",
    )
    (tmp_path / "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md").write_text(
        "ATOMICITY DEBT: 10 OPEN",
        encoding="utf-8",
    )

    errors = truth_drift_errors(tmp_path)

    assert any("OCP-DOC-DRIFT-001" in error for error in errors)
