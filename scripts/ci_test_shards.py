"""Balance integration test files without changing pytest's collection or exclusion policy.

Refresh timings from a complete JUnit report (including failed tests):
  python scripts/ci_test_shards.py refresh --junit test-results/python.xml
Preview all groups or select the JSON file list for one zero-based group:
  python scripts/ci_test_shards.py plan --count 8
  python scripts/ci_test_shards.py select --count 8 --index 0

Only repository-relative integration paths and elapsed seconds are persisted. New files use
the median positive measured duration (one second if none exist), then join the lightest group.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = Path("tests/ci/integration_durations.json")


def is_test_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        value == path.as_posix()
        and "\\" not in value
        and path.parts[:2] == ("tests", "integration")
        and ".." not in path.parts
        and path.suffix == ".py"
        and (path.name.startswith("test_") or path.name.endswith("_test.py"))
    )


def discover(root: Path) -> list[str]:
    directory = root / "tests/integration"
    if not directory.is_dir():
        raise ValueError("integration test directory is missing")
    files = sorted(
        p.relative_to(root).as_posix()
        for p in directory.rglob("*.py")
        if p.is_file() and is_test_path(p.relative_to(root).as_posix())
    )
    if not files:
        raise ValueError("no integration test files found")
    return files


def load_durations(path: Path) -> dict[str, float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("duration table must be a path-to-seconds object")
    result: dict[str, float] = {}
    for key, value in cast(dict[object, object], raw).items():
        if not isinstance(key, str) or not is_test_path(key):
            raise ValueError("duration table contains a non-integration path")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("duration must be a number")
        if not math.isfinite(value) or value < 0:
            raise ValueError("duration must be finite and nonnegative")
        result[key] = float(value)
    return result


def validate_partition(files: Sequence[str], groups: Sequence[Sequence[str]]) -> None:
    counts = Counter(path for group in groups for path in group)
    if len(set(files)) != len(files) or set(counts) != set(files):
        raise ValueError("shards must cover exactly the discovered integration files")
    if any(count != 1 for count in counts.values()):
        raise ValueError("an integration file appears in multiple shards")


def build_shards(
    files: Sequence[str], durations: Mapping[str, float], count: int
) -> tuple[list[list[str]], list[float]]:
    if count < 1:
        raise ValueError("shard count must be positive")
    known = [path for path in files if path in durations]
    missing = sorted(set(files) - durations.keys())
    measured = [durations[path] for path in known if durations[path] > 0]
    fallback = statistics.median(measured) if measured else 1.0
    groups: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    ordered = [*sorted(known, key=lambda path: (-durations[path], path)), *missing]
    for path in ordered:
        index = min(range(count), key=lambda index: (loads[index], index))
        groups[index].append(path)
        loads[index] += durations.get(path, fallback)
    validate_partition(files, groups)
    return [sorted(group) for group in groups], loads


def durations_from_junit(report: Path, files: Sequence[str]) -> dict[str, float]:
    modules = {path[:-3].replace("/", "."): path for path in files}
    totals: dict[str, float] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        classname = case.get("classname", "")
        filename = case.get("file", "").replace("\\", "/")
        if filename in files:
            path = filename
        elif classname.startswith("tests.integration."):
            matches = [
                name for name in modules if classname == name or classname.startswith(name + ".")
            ]
            if not matches:
                raise ValueError("integration JUnit case could not be mapped to a test file")
            path = modules[max(matches, key=len)]
        else:
            continue
        seconds = float(case.get("time", "0"))
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("JUnit duration must be finite and nonnegative")
        totals[path] = totals.get(path, 0.0) + seconds
    if not totals:
        raise ValueError("JUnit report contains no integration test durations")
    return {path: round(seconds, 3) for path, seconds in sorted(totals.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("plan", "select", "refresh"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--durations", type=Path, default=TIMINGS)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--index", type=int)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args(argv)
    try:
        files = discover(args.root)
        timing_path = args.root / args.durations
        if args.command == "refresh":
            if args.junit is None:
                raise ValueError("refresh requires --junit")
            durations = durations_from_junit(args.junit, files)
            timing_path.parent.mkdir(parents=True, exist_ok=True)
            timing_path.write_text(
                json.dumps(durations, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            print(f"Recorded timings for {len(durations)} integration files.")
            return 0
        durations = load_durations(timing_path)
        groups, loads = build_shards(files, durations, args.count)
        if args.command == "select":
            if args.index is None or not 0 <= args.index < args.count:
                raise ValueError("select requires an index within the shard count")
            if not groups[args.index]:
                raise ValueError("selected shard is empty; refusing an unscoped pytest run")
            print(json.dumps(groups[args.index]))
        else:
            print(
                json.dumps(
                    {
                        "file_count": len(files),
                        "unmeasured_files": sorted(set(files) - durations.keys()),
                        "shards": [
                            {"index": i, "estimated_seconds": round(loads[i], 3), "files": group}
                            for i, group in enumerate(groups)
                        ],
                    },
                    indent=2,
                )
            )
    except (OSError, ValueError, ET.ParseError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
