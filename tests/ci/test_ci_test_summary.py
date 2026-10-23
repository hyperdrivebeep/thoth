"""Missing artifacts, failed jobs and duplicate tests cannot produce a passing summary."""

import sys
from pathlib import Path

import pytest
from scripts.ci_test_summary import main, summarize


def write_report(path: Path, name: str, detail: str = "") -> None:
    path.write_text(
        f'<testsuites><testsuite><testcase classname="tests.example" name="{name}">'
        f"{detail}</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )


def test_summary_counts_every_report_and_keeps_skips_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_report(tmp_path / "python-fast.xml", "fast", "<skipped/>")
    for i in range(16):
        write_report(tmp_path / f"python-integration-{i}.xml", f"integration-{i}")
    report, passed = summarize(tmp_path, 16, ["success", "success"])
    assert passed and "| **Total** | 17 | 0 | 0 | 1 |" in report
    assert all(f"python-integration-{i}" in report for i in range(16))
    monkeypatch.setattr(
        sys,
        "argv",
        ["ci_test_summary", "--reports", str(tmp_path), "--job-results", "success", "success"],
    )
    assert main() == 0
    assert "| **Total** | 17 | 0 | 0 | 1 |" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped"])
def test_job_failure_is_not_overridden_by_passing_xml(tmp_path: Path, status: str) -> None:
    write_report(tmp_path / "python-fast.xml", "fast")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    _, passed = summarize(tmp_path, 1, ["success", status])
    assert not passed


@pytest.mark.parametrize("detail", ["<failure/>", "<error/>"])
def test_junit_failure_is_not_overridden_by_job_status(tmp_path: Path, detail: str) -> None:
    write_report(tmp_path / "python-fast.xml", "fast", detail)
    write_report(tmp_path / "python-integration-0.xml", "integration")
    report, passed = summarize(tmp_path, 1, ["success", "success"])
    assert not passed and "failures or errors" in report


def test_missing_invalid_and_empty_reports_fail(tmp_path: Path) -> None:
    (tmp_path / "python-fast.xml").write_text("bad XML", encoding="utf-8")
    report, passed = summarize(tmp_path, 1, ["success", "success"])
    assert not passed and "MISSING/INVALID" in report
    (tmp_path / "python-fast.xml").write_text("<testsuites/>", encoding="utf-8")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    report, passed = summarize(tmp_path, 1, ["success", "success"])
    assert not passed and "Empty report" in report


def test_cross_shard_duplicate_is_a_failure(tmp_path: Path) -> None:
    write_report(tmp_path / "python-fast.xml", "same")
    write_report(tmp_path / "python-integration-0.xml", "same")
    report, passed = summarize(tmp_path, 1, ["success", "success"])
    assert not passed and "Duplicate test identity" in report
