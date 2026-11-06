"""Missing artifacts, failed jobs and duplicate tests cannot produce a passing summary."""

import sys
from pathlib import Path

import pytest
from scripts.ci_test_summary import main, summarize

OK = ["success", "success", "success"]


def write_report(path: Path, name: str, detail: str = "") -> None:
    path.write_text(
        f'<testsuites><testsuite><testcase classname="tests.example" name="{name}">'
        f"{detail}</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )


def write_local(directory: Path, detail: str = "") -> None:
    write_report(directory / "python-local-retrieval.xml", "local", detail)


def test_summary_counts_every_report_and_keeps_skips_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_report(tmp_path / "python-fast.xml", "fast", "<skipped/>")
    for i in range(16):
        write_report(tmp_path / f"python-integration-{i}.xml", f"integration-{i}")
    write_local(tmp_path)
    report, passed = summarize(tmp_path, 16, OK)
    assert passed and "| **Total** | 18 | 0 | 0 | 1 |" in report
    assert all(f"python-integration-{i}" in report for i in range(16))
    assert "| python-local-retrieval | 1 | 0 | 0 | 0 |" in report
    monkeypatch.setattr(
        sys,
        "argv",
        ["ci_test_summary", "--reports", str(tmp_path), "--job-results", *OK],
    )
    assert main() == 0
    assert "| **Total** | 18 | 0 | 0 | 1 |" in capsys.readouterr().out


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped"])
def test_job_failure_is_not_overridden_by_passing_xml(
    tmp_path: Path, position: int, status: str
) -> None:
    write_report(tmp_path / "python-fast.xml", "fast")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    write_local(tmp_path)
    results = [*OK]
    results[position] = status
    _, passed = summarize(tmp_path, 1, results)
    assert not passed


@pytest.mark.parametrize("detail", ["<failure/>", "<error/>"])
def test_junit_failure_is_not_overridden_by_job_status(tmp_path: Path, detail: str) -> None:
    write_report(tmp_path / "python-fast.xml", "fast", detail)
    write_report(tmp_path / "python-integration-0.xml", "integration")
    write_local(tmp_path)
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "failures or errors" in report


def test_missing_invalid_and_empty_reports_fail(tmp_path: Path) -> None:
    (tmp_path / "python-fast.xml").write_text("bad XML", encoding="utf-8")
    write_local(tmp_path)
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "MISSING/INVALID" in report
    (tmp_path / "python-fast.xml").write_text("<testsuites/>", encoding="utf-8")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "Empty report" in report


def test_a_missing_local_retrieval_report_fails(tmp_path: Path) -> None:
    write_report(tmp_path / "python-fast.xml", "fast")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "Missing or invalid report: python-local-retrieval" in report


def test_skipped_local_retrieval_tests_fail_the_summary(tmp_path: Path) -> None:
    write_report(tmp_path / "python-fast.xml", "fast")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    write_local(tmp_path, "<skipped/>")
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "numpy 없음(local-retrieval extra)" in report


def test_cross_shard_duplicate_is_a_failure(tmp_path: Path) -> None:
    write_report(tmp_path / "python-fast.xml", "same")
    write_report(tmp_path / "python-integration-0.xml", "same")
    write_local(tmp_path)
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "Duplicate test identity" in report


def test_a_local_retrieval_test_that_also_ran_in_another_job_is_a_failure(tmp_path: Path) -> None:
    write_report(tmp_path / "python-fast.xml", "local")
    write_report(tmp_path / "python-integration-0.xml", "integration")
    write_local(tmp_path)
    report, passed = summarize(tmp_path, 1, OK)
    assert not passed and "Duplicate test identity" in report
