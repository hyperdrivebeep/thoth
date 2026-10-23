"""Summarize all Python JUnit reports; incomplete or failed jobs never become a pass."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(directory: Path, shards: int, job_results: list[str]) -> tuple[str, bool]:
    names = ["python-fast", *(f"python-integration-{i}" for i in range(shards))]
    lines = [
        "## Python test results",
        "",
        "| Job | Tests | Failures | Errors | Skipped |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    totals = [0, 0, 0, 0]
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for name in names:
        try:
            cases = list(ET.parse(directory / f"{name}.xml").getroot().iter("testcase"))
        except (OSError, ET.ParseError):
            lines.append(f"| {name} | MISSING/INVALID | — | — | — |")
            problems.append(f"Missing or invalid report: {name}")
            continue
        counts = [
            len(cases),
            sum(c.find("failure") is not None for c in cases),
            sum(c.find("error") is not None for c in cases),
            sum(c.find("skipped") is not None for c in cases),
        ]
        lines.append(f"| {name} | " + " | ".join(str(n) for n in counts) + " |")
        totals = [a + b for a, b in zip(totals, counts, strict=True)]
        if not cases:
            problems.append(f"Empty report: {name}")
        for case in cases:
            identity = (case.get("classname", ""), case.get("name", ""))
            if identity in seen:
                problems.append(f"Duplicate test identity in {name}")
            seen.add(identity)
    lines.append("| **Total** | " + " | ".join(str(n) for n in totals) + " |")
    if len(job_results) != 2 or any(status != "success" for status in job_results):
        problems.append("At least one Python job did not succeed.")
    if totals[1] or totals[2]:
        problems.append("JUnit reports contain failures or errors.")
    lines.extend(["", *(f"- {problem}" for problem in dict.fromkeys(problems))])
    lines.extend(["", "Result: " + ("FAIL / incomplete" if problems else "PASS"), ""])
    return "\n".join(lines), not problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--job-results", nargs=2, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    if args.shards < 1:
        parser.error("--shards must be positive")
    report, passed = summarize(args.reports, args.shards, args.job_results)
    print(report)
    if args.markdown_output:
        with args.markdown_output.open("a", encoding="utf-8") as stream:
            stream.write(report)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
