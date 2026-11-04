"""Staged upload names keep Chinese, Japanese and Korean characters and drop only unsafe ones."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from thoth.adapters.http.file_staging import safe_upload_name


def staged(name: str) -> str:
    return safe_upload_name(name, Path(name).suffix.lower())


@pytest.mark.parametrize(
    "name",
    ["雷达报告.pdf", "報告書.docx", "調査結果.csv", "탐지율-보고서.md", "plan.md", "report_v2.txt"],
)
def test_names_in_any_script_are_kept(name: str) -> None:
    assert staged(name) == name


@pytest.mark.parametrize(
    "name,expected",
    [
        ("報告 2026 (최종).docx", "報告-2026-최종-.docx"),
        ("a<b>:c|d?e*f.txt", "a-b-c-d-e-f.txt"),
        ("..\\up\\x.md", "up-x.md"),
        ("dir/sub/y.md", "dir-sub-y.md"),
        ("plan  final.md", "plan-final.md"),
    ],
)
def test_unsafe_characters_become_one_hyphen_per_run(name: str, expected: str) -> None:
    assert staged(name) == expected


def test_names_are_stored_in_composed_form() -> None:
    decomposed = unicodedata.normalize("NFD", "한글보고서.txt")
    assert decomposed != "한글보고서.txt"
    assert staged(decomposed) == "한글보고서.txt"


def test_control_and_direction_override_characters_never_reach_the_file_name() -> None:
    assert staged("\u202etxt.exe.txt") == "txt.exe.txt"
    assert staged("a\x00b\u200bc.txt") == "a-b-c.txt"


def test_long_names_are_cut_to_48_characters_before_the_extension() -> None:
    assert staged("가" * 80 + ".txt") == "가" * 48 + ".txt"


def test_a_name_with_nothing_safe_falls_back_to_source() -> None:
    assert safe_upload_name("???", ".md") == "source.md"
