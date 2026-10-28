"""G3 2: the glossary file is read as it is delivered, and the same entries give the same digest."""

from pathlib import Path

import pytest

from thoth.adapters import retrieval_glossary
from thoth.adapters.retrieval_glossary import (
    GLOSSARY_RELATIVE_PATH,
    GlossaryFileError,
    GlossaryFileReader,
    load_glossary,
    parse_glossary_csv,
)
from thoth.domain.glossary import MAX_ENTRIES, Glossary, make_entry

HEADER = "ko,en,domain,source,source_license,ambiguity,confidence,source_anchor,reuse_status"
ROWS = (
    "중앙값,median,perf,docs A,CC0,none,HIGH,s1,OK",
    "실행 시간,time per run,perf,docs B,CC0,none,MEDIUM,s2,OK",
)


def delivered(*rows: str, header: str = HEADER) -> str:
    return "\n".join((header, *rows)) + "\n"


def write(path: Path, text: str, *, bom: bool = False) -> Path:
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


def test_the_file_is_expected_in_the_workspace_under_its_own_folder() -> None:
    assert GLOSSARY_RELATIVE_PATH.parts == ("retrieval-glossary", "glossary.csv")


def test_a_byte_order_mark_does_not_change_the_glossary(tmp_path: Path) -> None:
    text = delivered(*ROWS)
    plain = load_glossary(write(tmp_path / "a.csv", text))
    marked = load_glossary(write(tmp_path / "b.csv", text, bom=True))
    assert plain is not None and marked is not None
    assert plain.digest == marked.digest and plain.entries == marked.entries
    assert plain.verified and len(plain.entries) == 2
    # The first column name is read as "ko" with the mark too.
    assert parse_glossary_csv("\ufeff" + text).digest == plain.digest


def test_only_the_four_columns_count_so_the_prose_around_them_does_not_move_the_digest() -> None:
    first = parse_glossary_csv(delivered(*ROWS))
    other = parse_glossary_csv(
        delivered(
            "중앙값,median,perf,another source,MIT,notes,HIGH,s9,BLOCKED",
            "실행 시간,time per run,perf,x,y,z,MEDIUM,q,r",
        )
    )
    assert first.digest == other.digest
    lean = parse_glossary_csv(
        "confidence,domain,en,ko\nHIGH,perf,median,중앙값\nMEDIUM,perf,time per run,실행 시간\n"
    )
    assert lean.digest == first.digest


def test_the_order_of_the_rows_does_not_change_the_digest() -> None:
    assert (
        parse_glossary_csv(delivered(*ROWS)).digest
        == parse_glossary_csv(delivered(*reversed(ROWS))).digest
    )


def test_a_term_is_known_by_its_content_not_by_how_its_spaces_or_letters_were_typed() -> None:
    spaced = parse_glossary_csv(delivered("  실행   시간 ,time   per run, perf ,s,l,n,medium,a,b"))
    (entry,) = spaced.entries
    assert entry == make_entry("실행 시간", "time per run", "perf", "MEDIUM")


def test_the_same_entry_twice_is_one_entry_at_the_higher_confidence() -> None:
    low = "중앙값,median,perf,s,l,n,MEDIUM,a,b"
    high = "중앙값,median,perf,s,l,n,HIGH,a,b"
    for rows in ((low, high), (high, low), (high, high)):
        glossary = parse_glossary_csv(delivered(*rows))
        assert [e.confidence for e in glossary.entries] == ["HIGH"]
    assert [e.confidence for e in parse_glossary_csv(delivered(low, low)).entries] == ["MEDIUM"]
    # Another domain or another translation is another entry.
    assert len(parse_glossary_csv(delivered(low, "중앙값,median,db,s,l,n,HIGH,a,b")).entries) == 2


def test_a_file_without_a_needed_column_is_refused() -> None:
    for header in ("ko,en,domain", "ko,en,confidence", "en,domain,confidence", ""):
        with pytest.raises(GlossaryFileError) as raised:
            parse_glossary_csv(header + "\nx,y,z\n")
        assert raised.value.code == "GLOSSARY_COLUMNS_MISSING"


def test_a_row_that_cannot_be_used_refuses_the_whole_file() -> None:
    bad = (
        ",median,perf,s,l,n,HIGH,a,b",
        "중앙값,,perf,s,l,n,HIGH,a,b",
        "중앙값,median,,s,l,n,HIGH,a,b",
        "중앙값,median,perf,s,l,n,LOW,a,b",
        "중앙값,median,perf,s,l,n,,a,b",
        "가" * 201 + ",median,perf,s,l,n,HIGH,a,b",
    )
    for row in bad:
        with pytest.raises(GlossaryFileError) as raised:
            parse_glossary_csv(delivered(ROWS[0], row))
        assert raised.value.code == "GLOSSARY_ROW_INVALID", row


def test_a_list_larger_than_the_limit_is_refused_and_one_at_the_limit_is_read() -> None:
    def many(count: int) -> str:
        return delivered(*(f"용어{n:05d},term{n},perf,s,l,n,HIGH,a,b" for n in range(count)))

    assert len(parse_glossary_csv(many(MAX_ENTRIES)).entries) == MAX_ENTRIES
    with pytest.raises(GlossaryFileError) as raised:
        parse_glossary_csv(many(MAX_ENTRIES + 1))
    assert raised.value.code == "GLOSSARY_TOO_LARGE"


def test_a_file_that_is_not_utf8_is_refused_and_a_missing_file_is_no_glossary(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_bytes(delivered(*ROWS).encode("cp949"))
    with pytest.raises(GlossaryFileError) as raised:
        load_glossary(broken)
    assert raised.value.code == "GLOSSARY_NOT_UTF8"
    assert load_glossary(tmp_path / "missing.csv") is None


def test_the_reader_reads_the_file_again_only_when_it_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "glossary.csv"
    reads: list[Path] = []
    real = retrieval_glossary.load_glossary

    def counting(target: Path) -> Glossary | None:
        reads.append(target)
        return real(target)

    monkeypatch.setattr(retrieval_glossary, "load_glossary", counting)
    reader = GlossaryFileReader(path)
    assert reader() is None and reads == []
    write(path, delivered(ROWS[0]))
    first = reader()
    assert first is not None and reader() is first and len(reads) == 1
    write(path, delivered(*ROWS))
    second = reader()
    assert second is not None and second.digest != first.digest and len(reads) == 2
    path.unlink()
    assert reader() is None
    write(path, delivered(ROWS[0]))
    again = reader()
    assert again is not None and again.digest == first.digest and len(reads) == 3
