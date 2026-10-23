"""The CI partition includes each real integration file exactly once."""

import json
from pathlib import Path

import pytest
from scripts.ci_test_shards import (
    build_shards,
    discover,
    durations_from_junit,
    load_durations,
    main,
    validate_partition,
)


def test_largest_files_are_balanced_deterministically() -> None:
    times = {f"tests/integration/test_{i}.py": n for i, n in enumerate([90, 80, 70, 60, 50, 40])}
    groups, loads = build_shards(list(times), times, 3)
    assert loads == [130, 130, 130]
    assert build_shards(list(reversed(times)), dict(reversed(list(times.items()))), 3) == (
        groups,
        loads,
    )
    validate_partition(list(times), groups)


def test_new_file_joins_lightest_group_and_removed_timing_is_ignored() -> None:
    times = {
        "tests/integration/test_long.py": 20.0,
        "tests/integration/test_short.py": 5.0,
        "tests/integration/test_deleted.py": 999.0,
    }
    files = [
        "tests/integration/test_long.py",
        "tests/integration/test_short.py",
        "tests/integration/test_new.py",
    ]
    groups, loads = build_shards(files, times, 2)
    assert groups[1] == ["tests/integration/test_new.py", "tests/integration/test_short.py"]
    assert loads == [20.0, 17.5]
    validate_partition(files, groups)


def test_all_new_files_have_a_positive_balancing_estimate() -> None:
    files = [f"tests/integration/test_{i}.py" for i in range(8)]
    groups, loads = build_shards(files, {}, 8)
    assert loads == [1.0] * 8 and all(len(group) == 1 for group in groups)


@pytest.mark.parametrize("groups", [[["a"], []], [["a"], ["a", "b"]], [["a", "b", "c"]]])
def test_missing_duplicate_or_extra_file_is_rejected(groups: list[list[str]]) -> None:
    with pytest.raises(ValueError):
        validate_partition(["a", "b"], groups)


def test_discovery_uses_both_pytest_filename_patterns_and_nested_directories(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "tests/integration/nested"
    directory.mkdir(parents=True)
    for name in ("test_alpha.py", "beta_test.py", "helper.py", "conftest.py"):
        (directory / name).write_text("", encoding="utf-8")
    assert discover(tmp_path) == [
        "tests/integration/nested/beta_test.py",
        "tests/integration/nested/test_alpha.py",
    ]


@pytest.mark.parametrize(
    "table",
    [
        [],
        {"/private/test_bad.py": 1},
        {"tests/integration/../test_bad.py": 1},
        {"tests/integration/test_a.py": -1},
        {"tests/integration/test_a.py": float("nan")},
        {"tests/integration/test_a.py": True},
    ],
)
def test_bad_duration_metadata_is_not_silently_used(tmp_path: Path, table: object) -> None:
    path = tmp_path / "durations.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(ValueError):
        load_durations(path)


def test_junit_refresh_counts_failed_cases_and_class_methods_without_private_paths(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        """<testsuites><testsuite>
      <testcase classname="tests.integration.test_a" name="test_one" time="2.5"/>
      <testcase classname="tests.integration.test_a.TestGroup" name="test_two" time="3">
        <failure/>
      </testcase>
      <testcase classname="tests.unit.test_x" name="test_fast" time="40"/>
    </testsuite></testsuites>""",
        encoding="utf-8",
    )
    assert durations_from_junit(report, ["tests/integration/test_a.py"]) == {
        "tests/integration/test_a.py": 5.5
    }
    with pytest.raises(ValueError, match="mapped"):
        durations_from_junit(report, ["tests/integration/test_other.py"])
    directory = tmp_path / "tests/integration"
    directory.mkdir(parents=True)
    (directory / "test_a.py").write_text("", encoding="utf-8")
    assert main(["refresh", "--root", str(tmp_path), "--junit", str(report)]) == 0
    persisted = (tmp_path / "tests/ci/integration_durations.json").read_bytes()
    assert b"\r\n" not in persisted
    assert json.loads(persisted) == {"tests/integration/test_a.py": 5.5}


def test_select_revalidates_actual_files_and_refuses_empty_shard(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "tests/integration"
    directory.mkdir(parents=True)
    (directory / "test_new.py").write_text("", encoding="utf-8")
    timings = tmp_path / "durations.json"
    timings.write_text("{}", encoding="utf-8")
    args = ["select", "--root", str(tmp_path), "--durations", str(timings), "--count", "2"]
    assert main([*args, "--index", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == ["tests/integration/test_new.py"]
    with pytest.raises(SystemExit) as error:
        main([*args, "--index", "1"])
    assert error.value.code == 2


def test_real_repository_partition_is_complete_and_disjoint() -> None:
    root = Path(__file__).resolve().parents[2]
    files = discover(root)
    groups, _ = build_shards(files, load_durations(root / "tests/ci/integration_durations.json"), 8)
    validate_partition(files, groups)
    assert sum(map(len, groups)) == len(files)
