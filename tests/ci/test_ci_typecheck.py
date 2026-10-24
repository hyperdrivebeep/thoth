"""Exercise CI decisions with injected checker responses; never launch basedpyright here."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import ci_typecheck
from scripts.ci_typecheck import BASELINE, baseline_entries, check


def baseline(count: int) -> bytes:
    return json.dumps(
        {
            "files": {
                "src/example.py": [
                    {
                        "code": "reportArgumentType",
                        "range": {"startColumn": i, "endColumn": i + 1, "lineCount": 1},
                    }
                    for i in range(count)
                ]
            }
        }
    ).encode()


def diagnostics(errors: int = 0, warnings: int = 0) -> str:
    return json.dumps(
        {
            "version": "1.39.10",
            "generalDiagnostics": [
                {
                    "file": "src/example.py",
                    "severity": severity,
                    "message": "new | type\nproblem",
                    "rule": "reportArgumentType",
                    "range": {"start": {"line": i, "character": 0}},
                }
                for severity, count in (("error", errors), ("warning", warnings))
                for i in range(count)
            ],
            "summary": {
                "filesAnalyzed": 12,
                "errorCount": errors,
                "warningCount": warnings,
                "informationCount": 0,
            },
        }
    )


class FakeChecker:
    def __init__(self, responses: list[tuple[int, str, bytes | None]]) -> None:
        self.responses = responses
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        assert command[0] == "checker-must-not-exist"
        assert command[command.index("--project") + 1] == str(root / "pyproject.toml")
        code, output, updated = self.responses.pop(0)
        if updated is not None:
            Path(command[command.index("--baselinefile") + 1]).write_bytes(updated)
        return subprocess.CompletedProcess(command, code, output, "checker stderr")


def run(tmp_path: Path, fake: FakeChecker, old: bytes | None = None) -> dict[str, Any]:
    if old is not None:
        path = tmp_path / BASELINE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(old)
    return check(
        tmp_path,
        tmp_path / "artifacts",
        checker=Path("checker-must-not-exist"),
        runner=fake,
        provenance={"commit": "a" * 40, "basedpyright_version": "1.39.10"},
    )


def test_missing_baseline_reports_debt_and_preserves_reviewable_generation(tmp_path: Path) -> None:
    fake = FakeChecker([(1, diagnostics(2), None), (0, diagnostics(), baseline(2))])
    result = run(tmp_path, fake)
    assert result["mode"] == "REPORT_ONLY"
    assert result["status"] == "REPORTED_NOT_ENFORCED" and result["exit_code"] == 0
    assert result["diagnostics"]["summary"]["errorCount"] == 2
    assert "--writebaseline" not in fake.commands[0]
    assert "--writebaseline" in fake.commands[1]
    assert not (tmp_path / BASELINE).exists()
    out = tmp_path / "artifacts"
    assert (out / "baseline-candidate.json").read_bytes() == baseline(2)
    receipt = json.loads((out / "provenance.json").read_text())
    assert receipt["commit"] == "a" * 40 and len(receipt["commands"]) == 2
    summary = (out / "summary.md").read_text()
    assert "not a type-check pass" in summary and "new \\| type<br>problem" in summary


def test_enforcement_allows_existing_debt_without_recreating_baseline(tmp_path: Path) -> None:
    fake = FakeChecker([(0, diagnostics(), None)])
    result = run(tmp_path, fake, baseline(2))
    assert result["mode"] == "ENFORCE" and result["status"] == "PASS"
    assert result["baseline_count"] == 2
    assert "--baselinemode=auto" in fake.commands[0]
    assert "--writebaseline" not in fake.commands[0] and len(fake.commands) == 1
    assert (tmp_path / BASELINE).read_bytes() == baseline(2)


@pytest.mark.parametrize("errors,warnings", [(1, 0), (0, 1)])
def test_native_new_diagnostics_fail_even_when_total_matches_old_count(
    tmp_path: Path, errors: int, warnings: int
) -> None:
    # Native output is already filtered: do not subtract the old count from it again.
    fake = FakeChecker([(1, diagnostics(errors, warnings), None)])
    result = run(tmp_path, fake, baseline(1))
    assert result["exit_code"] == 1 and result["status"] == "FAIL"
    assert len(fake.commands) == 1 and "--writebaseline" not in fake.commands[0]


def test_reduced_baseline_is_only_an_artifact_and_does_not_fail(tmp_path: Path) -> None:
    fake = FakeChecker([(0, diagnostics(), baseline(1))])
    result = run(tmp_path, fake, baseline(2))
    assert result["status"] == "PASS" and result["removed_count"] == 1
    assert (tmp_path / BASELINE).read_bytes() == baseline(2)
    assert (tmp_path / "artifacts/baseline-reduced-candidate.json").read_bytes() == baseline(1)


def test_auto_cannot_expand_the_approved_baseline(tmp_path: Path) -> None:
    result = run(tmp_path, FakeChecker([(0, diagnostics(), baseline(2))]), baseline(1))
    assert result["status"] == "ERROR" and "expanded" in result["problem"]
    assert (tmp_path / BASELINE).read_bytes() == baseline(1)


@pytest.mark.parametrize("code", [2, 3, 4, -1])
def test_checker_failure_is_not_downgraded_in_initial_reporting_mode(
    tmp_path: Path, code: int
) -> None:
    fake = FakeChecker([(code, "not diagnostic JSON", None)])
    result = run(tmp_path, fake)
    assert result["exit_code"] == 1 and result["status"] == "ERROR"
    assert len(fake.commands) == 1
    assert (tmp_path / "artifacts/diagnostics.stdout.txt").read_text() == "not diagnostic JSON"
    assert (tmp_path / "artifacts/summary.md").exists()


@pytest.mark.parametrize("raw", ["", "null", "{}", '{"summary":{},"generalDiagnostics":[]}'])
def test_missing_or_invalid_json_fails(tmp_path: Path, raw: str) -> None:
    assert run(tmp_path, FakeChecker([(0, raw, None)]))["status"] == "ERROR"


@pytest.mark.parametrize("field,value", [("filesAnalyzed", 0), ("errorCount", 3)])
def test_empty_or_inconsistent_analysis_fails(tmp_path: Path, field: str, value: int) -> None:
    raw = json.loads(diagnostics())
    raw["summary"][field] = value
    assert run(tmp_path, FakeChecker([(0, json.dumps(raw), None)]))["status"] == "ERROR"


@pytest.mark.parametrize("raw", [b"bad", b"{}", b'{"files":{"src/a.py":[{}]}}'])
def test_corrupt_baseline_never_falls_back_to_reporting(tmp_path: Path, raw: bytes) -> None:
    fake = FakeChecker([])
    result = run(tmp_path, fake, raw)
    assert result["status"] == "ERROR" and not fake.commands


def test_missing_baseline_after_adoption_is_a_failure(tmp_path: Path) -> None:
    fake = FakeChecker([])
    result = check(
        tmp_path,
        tmp_path / "artifacts",
        checker=Path("checker-must-not-exist"),
        runner=fake,
        baseline_required=True,
    )
    assert result["mode"] == "ENFORCE" and result["status"] == "ERROR"
    assert not fake.commands


@pytest.mark.parametrize(
    "failure", [PermissionError("blocked"), subprocess.TimeoutExpired("fake", 1)]
)
def test_execution_block_and_timeout_produce_failure_artifacts(
    tmp_path: Path, failure: Exception
) -> None:
    def blocked(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
        raise failure

    result = check(tmp_path, tmp_path / "artifacts", checker=Path("unused"), runner=blocked)
    assert result["status"] == "ERROR" and result["exit_code"] == 1
    assert (tmp_path / "artifacts/provenance.json").exists()


def test_generation_failure_cannot_claim_a_usable_baseline(tmp_path: Path) -> None:
    fake = FakeChecker([(1, diagnostics(2), None), (3, "bad config", None)])
    result = run(tmp_path, fake)
    assert result["status"] == "ERROR" and result["exit_code"] == 1
    assert not (tmp_path / "artifacts/baseline-candidate.json").exists()


def test_empty_valid_baseline_still_enforces(tmp_path: Path) -> None:
    result = run(tmp_path, FakeChecker([(1, diagnostics(1), None)]), baseline(0))
    assert result["mode"] == "ENFORCE" and result["exit_code"] == 1
    assert baseline_entries(baseline(0)).total() == 0


def test_non_diagnostic_failure_and_lost_generation_fail(tmp_path: Path) -> None:
    fake = FakeChecker([(1, diagnostics(), None)])
    assert run(tmp_path, fake)["status"] == "ERROR"
    fake = FakeChecker([(1, diagnostics(1), None), (0, diagnostics(), baseline(0))])
    result = run(tmp_path, fake)
    assert result["status"] == "ERROR" and "lost" in result["problem"]


def test_bad_diagnostic_location_is_reported_without_summary_crash(tmp_path: Path) -> None:
    raw = json.loads(diagnostics(1))
    raw["generalDiagnostics"][0]["range"] = None
    result = run(tmp_path, FakeChecker([(1, json.dumps(raw), None)]))
    assert result["status"] == "ERROR"
    assert (tmp_path / "artifacts/summary.md").exists()


def test_native_13910_update_notice_is_kept_and_json_is_parsed(tmp_path: Path) -> None:
    notice = "updated ../../temp/baseline.json with 1 error (went down by 1)\n"
    fake = FakeChecker([(0, notice + diagnostics(), baseline(1))])
    result = run(tmp_path, fake, baseline(2))
    assert result["status"] == "PASS" and result["removed_count"] == 1
    assert (tmp_path / "artifacts/diagnostics.stdout.txt").read_text().startswith(notice)
    assert (
        json.loads((tmp_path / "artifacts/diagnostics.json").read_text())["summary"]["errorCount"]
        == 0
    )


def test_arbitrary_prefix_before_json_is_not_silently_discarded(tmp_path: Path) -> None:
    fake = FakeChecker([(0, "fatal setup failure\n" + diagnostics(), None)])
    assert run(tmp_path, fake, baseline(1))["status"] == "ERROR"


@pytest.mark.parametrize("adopted", [False, True])
def test_cli_records_provenance_and_rejects_removal_after_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adopted: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.basedpyright]\ntypeCheckingMode='strict'\n")
    (tmp_path / "uv.lock").write_text("version=1\n")
    monkeypatch.setattr(ci_typecheck, "__file__", str(tmp_path / "scripts/ci_typecheck.py"))
    monkeypatch.setattr(ci_typecheck, "version", lambda _name: "1.39.10")
    monkeypatch.setenv("TYPECHECK_BASE_SHA", "b" * 40)
    git_commands: list[list[str]] = []

    def git_only(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
        assert command[0] == "git", "The real checker must never start in this test"
        git_commands.append(command)
        code = 0 if adopted or command[-1] != f"{'b' * 40}:{BASELINE.as_posix()}" else 128
        return subprocess.CompletedProcess(command, code, "a" * 40 + "\n", "")

    monkeypatch.setattr(ci_typecheck, "execute", git_only)
    fake = FakeChecker(
        [] if adopted else [(0, diagnostics(), None), (0, diagnostics(), baseline(0))]
    )

    def fake_check(root: Path, output: Path, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["provenance"]["commit"] == "a" * 40
        kwargs["checker"] = Path("checker-must-not-exist")
        return check(root, output, runner=fake, **kwargs)

    monkeypatch.setattr(ci_typecheck, "check", fake_check)
    summary = tmp_path / "job-summary.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_typecheck",
            "--output",
            str(tmp_path / "artifacts"),
            "--markdown-output",
            str(summary),
        ],
    )
    assert ci_typecheck.main() == (1 if adopted else 0)
    assert "Mode:" in summary.read_text() and len(git_commands) == 3
    result = json.loads((tmp_path / "artifacts/result.json").read_text())
    assert result["mode"] == ("ENFORCE" if adopted else "REPORT_ONLY")


def test_workflow_has_independent_pinned_job_and_keeps_artifacts_on_failure() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load(
        (root / ".github/workflows/product-check.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["python-types"]
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == "60"
    assert "continue-on-error" not in job and "needs" not in job
    steps = job["steps"]
    assert steps[0]["with"]["fetch-depth"] == "0"
    assert any(s.get("with", {}).get("python-version") == "3.13" for s in steps)
    command = next(s["run"] for s in steps if s.get("name") == "Report or enforce Python types")
    assert "ci_typecheck.py" in command and "GITHUB_STEP_SUMMARY" in command
    upload = steps[-1]
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert all("continue-on-error" not in s for s in steps)
