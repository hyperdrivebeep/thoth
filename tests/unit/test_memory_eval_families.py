"""Recall by question family: the file format, the measures, the split and the sealed guard."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_memory_recall import correction, memory

from thoth.application.services.memory_recall import RecallLimits
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import FullMemoryRevision
from thoth.domain.memory_expansion import QueryExpansion

SCRIPTS = Path(__file__).parents[2] / "scripts"


def load_script(name: str) -> Any:
    """The script by file path, registered under its own name so the scripts share one copy."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fam: Any = load_script("memory_eval_families")
replay: Any = load_script("memory_selection_replay")
EXAMPLE = Path(__file__).parents[1] / "fixtures" / "memory_eval" / "families.example.jsonl"
QUESTION = "출처별 대조 분석(A1)을 지금 바로 해도 돼? 무엇이 더 필요해?"
A1_TEXT = "연결된 RFP PDF 원문과 표를 읽기 전용으로 대조한다."
WIDENING = QueryExpansion(
    synonyms=("원문 검토", "문서 비교"),
    related=("근거 확인",),
    note_line="원문을 읽기 전용으로 비교",
)


def line(family_id: str, kind: str, needed: list[str], **more: object) -> str:
    return json.dumps(
        {
            "family_id": family_id,
            "type": kind,
            "project": "project:t",
            "phrasings": [{"id": "original", "text": "질문 하나"}],
            "gold": {"needed": needed, **more},
        },
        ensure_ascii=False,
    )


def test_the_example_file_parses_and_has_three_phrasings_per_family() -> None:
    parsed = fam.parse_families(EXAMPLE.read_text(encoding="utf-8"))
    assert [f.type for f in parsed] == ["DIRECT", "ELLIPTIC_FOLLOWUP", "IRRELEVANT"]
    assert all(len(f.phrasings) == 3 and f.split == "dev" for f in parsed)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (line("A", "DIRECT", []), "needs at least one needed memory"),
        (line("A", "IRRELEVANT", ["m"]), "needs no memory"),
        (line("A", "DIRECT", ["m"], forbidden=["m"]), "both needed and forbidden"),
        (line("A", "CORRECTION", ["m"]), "a correction applies"),
        (line("A", "DIRECT", ["m"]) + "\n" + line("A", "DIRECT", ["m"]), "used twice"),
        ("not json", "line 1"),
    ],
)
def test_a_family_that_breaks_the_form_is_refused_with_its_line(text: str, needle: str) -> None:
    with pytest.raises(fam.FamilyError, match=needle):
        fam.parse_families(text)


def test_comments_and_blank_lines_are_skipped() -> None:
    parsed = fam.parse_families("# a note\n\n" + line("A", "DIRECT", ["m"]) + "\n")
    assert [f.family_id for f in parsed] == ["A"]


def test_the_split_comes_from_the_split_file_or_the_family_and_never_both_sets() -> None:
    parsed = fam.parse_families("\n".join(line(name, "DIRECT", ["m"]) for name in ("A", "B", "C")))
    by_file = fam.select_split(parsed, "sealed", {"dev": ["A"], "sealed": ["B", "C"]})
    assert [f.family_id for f in by_file] == ["B", "C"]
    with pytest.raises(fam.FamilyError, match="both splits"):
        fam.select_split(parsed, "dev", {"dev": ["A"], "sealed": ["A"]})
    with pytest.raises(fam.FamilyError, match="unknown families"):
        fam.select_split(parsed, "dev", {"dev": ["Z"]})
    assert fam.select_split(parsed, "dev", None) == []  # no split written on the families


def test_the_same_question_in_other_spacing_or_width_has_the_same_cache_key() -> None:
    assert fam.question_key("AI  규제는?") == fam.question_key("\uff21\uff29 규제는?")
    assert fam.question_key("a") != fam.question_key("b")


def result(family: Any, phrasing: str, included: list[FullMemoryRevision]) -> Any:
    chosen = next(p for p in family.phrasings if p.id == phrasing)
    return fam.score_question(family, chosen, included)


def family_of(family_id: str, kind: str, needed: list[str], forbidden: list[str]) -> Any:
    return fam.Family.model_validate(
        {
            "family_id": family_id,
            "type": kind,
            "project": "p",
            "phrasings": [{"id": f"p{n}", "text": f"q{n}"} for n in (1, 2, 3)],
            "gold": {
                "needed": needed,
                "forbidden": forbidden,
                "correction_applied": kind == "CORRECTION",
            },
        }
    )


def test_the_measures_are_counted_exactly_and_by_type() -> None:
    a, b, x = memory("a"), memory("b"), memory("x")
    fix, old = memory("fix"), memory("old")
    direct = family_of("F1", "DIRECT", ["memory:a", "memory:b"], [])
    plain = family_of("F2", "IRRELEVANT", [], [])
    corrected = family_of("F3", "CORRECTION", ["memory:fix"], ["memory:old"])
    results = [
        result(direct, "p1", [a, b]),
        result(direct, "p2", [a, x]),
        result(direct, "p3", []),
        result(plain, "p1", [x]),
        result(plain, "p2", []),
        result(corrected, "p1", [fix, old]),
    ]
    total = fam.summarize(results)
    assert total["questions"] == 6 and total["questions_that_need_memory"] == 4
    assert total["needed_recall_micro"] == 0.5714 and total["needed_recall_macro"] == 0.625
    assert total["all_needed_given_rate"] == 0.5 and total["family_complete_rate"] == 0.5
    assert total["memories_given"] == 7 and total["unwanted_share"] == 0.4286
    assert total["forbidden_injected"] == 1 and total["questions_with_unwanted"] == 3
    assert total["false_positive_rate"] == 0.5 and total["questions_that_need_none"] == 2
    by_type = fam.summarize_by_type(results)
    assert set(by_type) == {"DIRECT", "CORRECTION", "IRRELEVANT"}
    assert by_type["IRRELEVANT"]["false_positive_rate"] == 0.5
    assert by_type["IRRELEVANT"]["needed_recall_micro"] is None
    assert by_type["CORRECTION"]["forbidden_injected"] == 1
    assert by_type["DIRECT"]["family_complete_rate"] == 0.0


def a1_project() -> list[FullMemoryRevision]:
    return [
        memory("a1", kind=MemoryKind.ACTION, body=A1_TEXT, evidence=("span:1",)),
        memory("h2", body="표본이 작으면 결론을 보류한다", evidence=("span:1",), minutes=1),
        correction("fix", "도로 결과는 참고자료로만 쓴다", minutes=2),
    ]


def a1_family(split: str | None = None) -> Any:
    return fam.Family.model_validate(
        {
            "family_id": "F-A1",
            "type": "REPHRASED",
            "project": "project:t",
            "split": split,
            "phrasings": [{"id": "original", "text": QUESTION}],
            "gold": {"needed": ["memory:a1"]},
        }
    )


def setup(cache: dict[str, QueryExpansion]) -> Any:
    return fam.RunSetup(replay.family_player(RecallLimits()), cache)


def test_the_three_conditions_run_and_a_missing_expansion_is_counted_not_assumed() -> None:
    report = fam.evaluate([a1_family()], {"project:t": a1_project()}, setup({}))
    blocks = report["conditions"]
    assert blocks["off"]["overall"]["needed_recall_micro"] == 0.0
    assert blocks["off"]["overall"]["memories_given"] == 0
    # one question word ("대조") is met, an automatic memory needs two: not given without help
    assert blocks["current"]["overall"]["needed_recall_micro"] == 0.0
    assert blocks["expansion"]["overall"]["questions"] == 0
    assert blocks["expansion"]["overall"]["questions_missing_expansion"] == 1
    assert report["missing_expansions"] == {fam.question_key(QUESTION): QUESTION}


def test_cached_added_words_let_the_memory_in_and_the_question_is_not_asked_again() -> None:
    cache = {fam.question_key(QUESTION): WIDENING}
    report = fam.evaluate([a1_family()], {"project:t": a1_project()}, setup(cache))
    block = report["conditions"]["expansion"]["overall"]
    assert block["needed_recall_micro"] == 1.0 and block["questions_missing_expansion"] == 0
    assert report["missing_expansions"] == {}
    row = report["questions"]["expansion"][0]
    assert row["matched_by"] == {"기타(a1)": "EXPANSION"} and row["unwanted"] == []


def test_a_family_naming_a_project_with_no_memories_loaded_is_refused() -> None:
    with pytest.raises(fam.FamilyError, match="no memories loaded"):
        fam.evaluate([a1_family()], {}, setup({}))


def make_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE memory_context_packs (project_id TEXT, content_json TEXT)")
        connection.execute(
            "CREATE TABLE memory_revision_ledger (project_id TEXT, content_json TEXT)"
        )
        for item in a1_project():
            stored = item.model_copy(update={"project_id": "project:t"})
            connection.execute(
                "INSERT INTO memory_revision_ledger VALUES (?, ?)",
                ("project:t", stored.model_dump_json()),
            )
        connection.commit()


def write_families(path: Path, split: str) -> None:
    path.write_text(a1_family(split).model_dump_json() + "\n", encoding="utf-8")


def command(tmp_path: Path, families_file: Path, split: str, *more: str) -> list[str]:
    return [
        "--source",
        str(tmp_path / "thoth.sqlite3"),
        "--copy",
        str(tmp_path / "copy.sqlite3"),
        "--families",
        str(families_file),
        "--split",
        split,
        "--output",
        str(tmp_path / f"{split}.json"),
        *more,
    ]


def test_the_command_line_runs_with_no_model_call_and_opens_a_sealed_set_once(
    tmp_path: Path,
) -> None:
    make_database(tmp_path / "thoth.sqlite3")
    sealed = tmp_path / "families.jsonl"
    write_families(sealed, "sealed")
    base = command(tmp_path, sealed, "sealed", "--conditions", "off,current")
    # not opened without the confirmation
    assert replay.main(base) == 2 and not (tmp_path / "sealed.json").exists()
    assert replay.main([*base, "--confirm-sealed"]) == 0
    written = json.loads((tmp_path / "sealed.json").read_text(encoding="utf-8"))
    assert written["meta"]["model_calls"] == 0 and written["meta"]["questions"] == 1
    assert set(written["conditions"]) == {"off", "current"}
    assert fam.sealed_marker(sealed).exists()
    # a second opening is refused unless it is said to turn the set into a development set
    assert replay.main([*base, "--confirm-sealed"]) == 2
    assert replay.main([*base, "--confirm-sealed", "--reopen-sealed"]) == 0
    # a development run needs no confirmation and leaves no marker
    other = tmp_path / "dev.jsonl"
    write_families(other, "dev")
    assert replay.main(command(tmp_path, other, "dev")) == 0
    assert not fam.sealed_marker(other).exists()


def test_the_database_is_never_written_and_the_missing_questions_are_listed(
    tmp_path: Path,
) -> None:
    make_database(tmp_path / "thoth.sqlite3")
    before = (tmp_path / "thoth.sqlite3").read_bytes()
    families_file = tmp_path / "families.jsonl"
    write_families(families_file, "dev")
    missing = tmp_path / "missing.json"
    arguments = command(tmp_path, families_file, "dev", "--missing-out", str(missing), "--markdown")
    assert replay.main(arguments) == 0
    assert (tmp_path / "thoth.sqlite3").read_bytes() == before
    assert json.loads(missing.read_text(encoding="utf-8")) == {fam.question_key(QUESTION): QUESTION}
    assert "| expansion | 전체 |" in (tmp_path / "dev.json").read_text(encoding="utf-8")
