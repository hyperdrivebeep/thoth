"""G3 1a: the scanner fixes identifiers first and splits only at Latin/digit-to-Hangul edges."""

import pytest

from thoth.domain.query_identifiers import QueryFields, fold, occurs, scan_query


def identifiers(fields: QueryFields) -> list[tuple[str, str]]:
    return [(i.text, i.kind) for i in fields.identifiers]


def tokens(fields: QueryFields) -> list[str]:
    return [t.text for t in fields.split_tokens]


def test_a_path_ends_at_its_extension_and_the_particle_after_it_is_split_off() -> None:
    fields = scan_query(r"C:\data\보고서.pdf를 열어줘")
    assert identifiers(fields) == [(r"C:\data\보고서.pdf", "PATH")]
    assert tokens(fields) == ["를", "열어줘"]
    assert [t.split for t in fields.split_tokens] == [True, False]


def test_a_path_without_an_extension_followed_by_hangul_keeps_both_forms() -> None:
    (path,) = scan_query(r"C:\data\report를 보자").identifiers
    assert path.boundary_ambiguous is True
    assert path.text == r"C:\data\report를"
    assert set(path.needles) == {r"c:/data/report를", "c:/data/report"}
    # A path whose last part is pure Latin or pure Hangul has nothing to be unsure about.
    for plain in (r"C:\data\report", r"C:\data\보고서"):
        (only,) = scan_query(plain + " 확인").identifiers
        assert only.boundary_ambiguous is False


@pytest.mark.parametrize(
    ("question", "found", "rest"),
    [
        ("gcc에서 실패", [], ["gcc", "에서", "실패"]),
        ("abort하고 끝났다", [], ["abort", "하고", "끝났다"]),
        ("17개가 실패", [], ["17", "개가", "실패"]),
        ("1.44에서 느리다", [("1.44", "VERSION")], ["에서", "느리다"]),
        ("v1.2와 v1.20의 차이", [("v1.2", "VERSION"), ("v1.20", "VERSION")], ["와", "의", "차이"]),
        ("fill_nan(None)이 느리다", [("fill_nan(None)", "SYMBOL")], ["이", "느리다"]),
        ("_BitInt(33) 시험", [("_BitInt(33)", "SYMBOL")], ["시험"]),
    ],
)
def test_the_planned_examples_split_as_designed(
    question: str, found: list[tuple[str, str]], rest: list[str]
) -> None:
    fields = scan_query(question)
    assert identifiers(fields) == found
    assert tokens(fields) == rest


def test_hangul_before_latin_and_hangul_inside_are_not_split() -> None:
    assert tokens(scan_query("보고서abc 확인")) == ["보고서abc", "확인"]
    assert tokens(scan_query("한글만있는말")) == ["한글만있는말"]
    assert tokens(scan_query("abc하고def")) == ["abc", "하고def"]


def test_the_question_is_never_changed_and_offsets_point_into_it() -> None:
    question = "DuckDB v2.0.0-alpha39998에서 fill_nan(None)이 느린 이유 gcc에서"
    fields = scan_query(question)
    assert fields.original is question and fields.normalized == question
    assert scan_query(question) == fields
    for item in fields.identifiers:
        assert question[item.start : item.end] == item.text
    for token in fields.split_tokens:
        assert question[token.start : token.end] == token.text


@pytest.mark.parametrize(
    ("question", "kind", "text"),
    [
        ("lerobot==0.3.3 로 올림", "PACKAGE", "lerobot==0.3.3"),
        ("CUDA_VISIBLE_DEVICES 설정", "ENV_VAR", "CUDA_VISIBLE_DEVICES"),
        ("--steps=100000 으로 실행", "FLAG", "--steps=100000"),
        ("CVE-2026-12345 참고", "REFERENCE", "CVE-2026-12345"),
        ("RFC 2119 참고", "REFERENCE", "RFC 2119"),
        ("10.1000/xyz123 논문", "REFERENCE", "10.1000/xyz123"),
        ("0.059s 걸림", "QUANTITY", "0.059s"),
        ("10_000행", "QUANTITY", "10_000"),
        ("test_etf_api.py 실패", "PATH", "test_etf_api.py"),
        ("samples/sensor/x.overlay 수정", "PATH", "samples/sensor/x.overlay"),
        ("accel_x 값", "SYMBOL", "accel_x"),
        ("pl.collect_all([lf, lf])가 패닉", "SYMBOL", "pl.collect_all([lf, lf])"),
        ("ism6hg256x@0 읽기", "SYMBOL", "ism6hg256x@0"),
        ("ESP32-S2 보드", "MIXED", "ESP32-S2"),
        ("u33 형", "MIXED", "u33"),
        ("2025.8에서", "VERSION", "2025.8"),
    ],
)
def test_each_kind_is_fixed_by_its_own_grammar(question: str, kind: str, text: str) -> None:
    assert (text, kind) in identifiers(scan_query(question))


def test_abbreviations_and_plain_words_are_not_identifiers() -> None:
    fields = scan_query("e.g. the sort is slow, i.e. very slow")
    assert fields.identifiers == ()
    assert tokens(fields) == ["e", "g", "the", "sort", "is", "slow", "i", "e", "very", "slow"]


def test_a_call_offers_its_name_as_a_second_needle() -> None:
    (call,) = scan_query("fill_nan(None)이").identifiers
    assert call.needles == ("fill_nan(none)", "fill_nan")
    (dotted,) = scan_query("pl.collect_all([lf, lf])").identifiers
    assert dotted.needles == ("pl.collect_all([lf, lf])", "pl.collect_all", "collect_all")


def test_a_version_matches_itself_and_not_a_longer_version() -> None:
    needle = "1.2"
    assert occurs(fold("version 1.2 was used"), needle, "VERSION")
    assert occurs(fold("버전 1.2에서"), needle, "VERSION")
    assert occurs(fold("tag v1.2 released"), needle, "VERSION")
    assert occurs(fold("done in 1.2."), needle, "VERSION")
    for longer in ("1.20", "1.2.0", "21.2", "1.2.3.4", "x1.2"):
        assert not occurs(fold(f"see {longer} here"), needle, "VERSION")
    assert not occurs(fold("version v1.20 here"), "v1.2", "VERSION")


def test_an_identifier_ends_where_a_letter_digit_or_underscore_would_continue_it() -> None:
    assert occurs(fold("GCC에서 실패"), "gcc", "MIXED")
    assert occurs(fold("gcc.gnu.org"), "gcc", "MIXED")
    assert not occurs(fold("gcc2 and agcc"), "gcc", "MIXED")
    assert not occurs(fold("__builtin_abort"), "abort", "MIXED")
    assert occurs(fold("fill_nan(None) is slow"), "fill_nan(none)", "SYMBOL")


def test_paths_match_across_separators_and_case() -> None:
    assert occurs(fold("open C:/Data/Report.PDF now"), "c:/data/report.pdf", "PATH")
    assert occurs(fold(r"open C:\Data\Report.PDF 를"), "c:/data/report.pdf", "PATH")
    assert not occurs(fold("C:/Data/Report.pdfx"), "c:/data/report.pdf", "PATH")
