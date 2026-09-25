from __future__ import annotations

import copy
from typing import Any

import pytest
from scripts.verification_profiles import (
    CONTROL_PATHS,
    policy_definition,
    select_manifest_profile,
)

BASELINE_ID = "b" * 64


def manifest() -> dict[str, Any]:
    paths = {*CONTROL_PATHS, "scripts/pytest_progress.py", "src/thoth/example.py"}
    return {
        "head": "1" * 40,
        "index_digest": "2" * 64,
        "repository_digest": "3" * 64,
        "files": [{"path": p, "sha256": "a" * 64, "size": 1} for p in sorted(paths)],
    }


def selected(changes: dict[str, str | None], **options: Any) -> dict[str, Any]:
    baseline = manifest()
    current = copy.deepcopy(baseline)
    entries = {item["path"]: item for item in current["files"]}
    for path, sha in changes.items():
        entries[path] = {"path": path, "sha256": sha, "size": None if sha is None else 2}
    current["files"] = [entries[p] for p in sorted(entries)]
    current["repository_digest"] = "4" * 64
    return select_manifest_profile(
        current,
        baseline=baseline,
        baseline_id=BASELINE_ID,
        baseline_policy=policy_definition(),
        **options,
    )


def test_actual_tool_diff_selects_complete_tooling_suite() -> None:
    selection = selected({"scripts/pytest_progress.py": "c" * 64})
    assert selection["profile"] == "TOOLING"
    assert selection["requires_full_suite"] is False
    assert selection["pytest_targets"] == ["tests/architecture"]
    assert selection["expected_stages"] == ["ARCHITECTURE", "LINT", "TYPE", "TOOLING"]
    assert selection["changed_files"][0]["path"] == "scripts/pytest_progress.py"


@pytest.mark.parametrize(
    "path",
    [
        "src/thoth/example.py",
        "apps/web/new.tsx",
        "uv.lock",
        "pyproject.toml",
        "pytest.ini",
        "tests/conftest.py",
        "tests/architecture/conftest.py",
        "tests/architecture/owner_helpers.py",
        "migrations/new.py",
        "schemas/new.json",
        "unknown.py",
        "docs/verification/executable.py",
    ],
)
def test_unknown_or_product_mixed_with_tools_is_full(path: str) -> None:
    assert selected({path: "c" * 64, "scripts/pytest_progress.py": "d" * 64})["profile"] == "FULL"


@pytest.mark.parametrize("path", sorted(CONTROL_PATHS))
def test_policy_bearing_change_never_selects_itself_for_tooling(path: str) -> None:
    assert selected({path: "c" * 64})["profile"] == "FULL"


def test_new_tool_test_and_companion_doc_are_recorded() -> None:
    result = selected(
        {
            "tests/architecture/test_new_reporting.py": "c" * 64,
            "docs/verification/report.md": "d" * 64,
        }
    )
    assert result["profile"] == "TOOLING"
    assert {c["kind"] for c in result["changed_files"]} == {"ADDED"}


def test_deletion_or_rename_cannot_shrink_verification() -> None:
    assert selected({"scripts/pytest_progress.py": None})["profile"] == "FULL"
    assert (
        selected({"scripts/pytest_progress.py": None, "scripts/renamed_reporter.py": "a" * 64})[
            "profile"
        ]
        == "FULL"
    )


def test_missing_baseline_or_policy_and_forced_full_cannot_shrink() -> None:
    current = manifest()
    assert select_manifest_profile(current)["profile"] == "FULL"
    assert (
        select_manifest_profile(current, baseline=manifest(), baseline_id=BASELINE_ID)["profile"]
        == "FULL"
    )
    assert selected({"scripts/pytest_progress.py": "c" * 64}, force_full=True)["profile"] == "FULL"


def test_rule_documents_are_not_ordinary_markdown() -> None:
    result = selected({"PROJECT_WIKI/rule.md": "c" * 64}, rule_documents=("PROJECT_WIKI/rule.md",))
    assert result["profile"] == "FULL"
