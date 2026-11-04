"""Legacy East Asian text is read as the right characters or refused, never kept garbled."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.adapters.parsers.text_encoding import (
    COMMON_SIMPLIFIED,
    declared_html_encoding,
    decode_text,
)
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.enums import AuthorityState, CutoffState, ParserErrorCode, SecurityClass
from thoth.domain.errors import ParserFailure

COMMA = "\uff0c"  # the Chinese comma
SAMPLES = {
    "gb18030": (
        "探测率是指在给定条件下正确发现目标的比例。本报告记录了二零二六年的试验结果"
        + COMMA
        + "并对雷达的虚警率与分辨率进行了比较。"
    ),
    "big5": (
        "探測率是指在給定條件下正確發現目標的比例。本報告記錄了二零二六年的試驗結果"
        + COMMA
        + "並對雷達的虛警率與解析度進行了比較。"
    ),
    "shift_jis": (
        "探知率とは、与えられた条件の下で目標を正しく発見できた割合のことです。"
        "この報告書は二〇二六年の試験結果を記録しました。"
    ),
    "euc_jp": (
        "探知率とは、与えられた条件の下で目標を正しく発見できた割合のことです。"
        "この報告書は二〇二六年の試験結果を記録しました。"
    ),
    "cp949": (
        "탐지율은 주어진 조건에서 목표를 올바르게 발견한 비율을 뜻합니다. "
        "이 보고서는 2026년 시험 결과를 기록했습니다."
    ),
}
CODEC = {
    "gb18030": "gb18030",
    "big5": "big5hkscs",
    "shift_jis": "cp932",
    "euc_jp": "euc_jp",
    "cp949": "cp949",
}


def artifact(raw: bytes, media_type: str, suffix: str) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        artifact_id=f"artifact:{hashlib.sha256(raw).hexdigest()[:16]}",
        project_id="project:test",
        source_uri=f"fixture{suffix}",
        media_type=media_type,
        byte_sha256=hashlib.sha256(raw).hexdigest(),
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=datetime(2026, 8, 30, tzinfo=UTC),
        parser_name="unparsed",
        parser_version="0",
    )


def parse(raw: bytes, media_type: str, suffix: str) -> StructuralDocument:
    return default_parser_registry().parse(artifact(raw, media_type, suffix), raw)


def texts(document: StructuralDocument) -> str:
    return "\n".join(node.text or "" for node in document.nodes)


def refusal(raw: bytes) -> str:
    with pytest.raises(ParserFailure) as caught:
        decode_text(raw)
    assert caught.value.code is ParserErrorCode.TEXT_ENCODING_UNCONFIRMED
    return str(caught.value)


@pytest.mark.parametrize("name", list(SAMPLES))
def test_undeclared_legacy_text_is_read_as_its_own_characters(name: str) -> None:
    sample = SAMPLES[name]
    decoded = decode_text(sample.encode(name))
    assert decoded.text == sample and decoded.encoding == CODEC[name]


@pytest.mark.parametrize("name", list(SAMPLES))
def test_text_file_keeps_the_characters_and_records_the_encoding(name: str) -> None:
    sample = SAMPLES[name]
    document = parse(sample.encode(name), "text/plain", ".txt")
    assert texts(document) == sample
    assert {w for node in document.nodes for w in node.extraction_warnings} == {
        f"TEXT_ENCODING:{CODEC[name]}"
    }


def test_csv_file_in_a_legacy_encoding_keeps_its_characters() -> None:
    raw = "조건,지연\n알파,12\n베타,15\n감마,18\n".encode("cp949")
    document = parse(raw, "text/csv", ".csv")
    assert "지연" in texts(document) and "알파" in texts(document)
    assert all(w == "TEXT_ENCODING:cp949" for n in document.nodes for w in n.extraction_warnings)


@pytest.mark.parametrize("name", list(SAMPLES))
def test_html_with_a_charset_declaration_follows_it(name: str) -> None:
    sample = SAMPLES[name]
    label = {
        "gb18030": "GB2312",
        "big5": "Big5",
        "shift_jis": "Shift_JIS",
        "euc_jp": "EUC-JP",
        "cp949": "euc-kr",
    }[name]
    raw = (
        f'<html><head><meta http-equiv="Content-Type" content="text/html; charset={label}">'
        f"<title>{sample[:8]}</title></head><body><h1>{sample[:8]}</h1><p>{sample}</p></body></html>"
    ).encode(name)
    assert declared_html_encoding(raw) == label.lower()
    document = parse(raw, "text/html", ".html")
    assert sample in texts(document)
    assert "\ufffd" not in texts(document)


@pytest.mark.parametrize("name", ["gb18030", "cp949"])
def test_html_without_a_declaration_is_read_when_only_one_encoding_fits(name: str) -> None:
    sample = SAMPLES[name]
    raw = f"<html><body><h1>{sample[:8]}</h1><p>{sample}</p></body></html>".encode(name)
    document = parse(raw, "text/html", ".html")
    assert sample in texts(document)
    assert any(f"TEXT_ENCODING:{CODEC[name]}" in n.extraction_warnings for n in document.nodes)


def test_declared_encoding_that_does_not_fit_the_bytes_is_refused() -> None:
    raw = (
        f'<html><head><meta charset="utf-8"></head><body><p>{SAMPLES["gb18030"]}</p></body></html>'
    )
    with pytest.raises(ParserFailure) as caught:
        parse(raw.encode("gb18030"), "text/html", ".html")
    assert caught.value.code is ParserErrorCode.TEXT_ENCODING_UNCONFIRMED


def test_valid_utf8_wins_over_a_conflicting_declaration() -> None:
    raw = f'<html><head><meta charset="gb2312"></head><body><p>{SAMPLES["cp949"]}</p></body></html>'
    document = parse(raw.encode("utf-8"), "text/html", ".html")
    assert SAMPLES["cp949"] in texts(document)
    assert all(
        not n.extraction_warnings or n.extraction_warnings == ("STATIC_HTML_ONLY",)
        for n in document.nodes
    )


@pytest.mark.parametrize("raw", [b"\xb0\xa1", b"\xd6\xd0\xce\xc4", b"\x81\x00abc", b"abc\xff\xfe"])
def test_short_or_unreadable_bytes_are_refused_not_guessed(raw: bytes) -> None:
    assert "no known encoding" in refusal(raw)


def test_bytes_that_two_encodings_read_cleanly_are_refused_and_both_are_named() -> None:
    # Common Chinese characters whose GB18030 bytes are also valid Korean Hangul syllables in CP949.
    chars = [c for c in sorted(COMMON_SIMPLIFIED) if _reads_as_hangul(c.encode("gb18030"))]
    assert len(chars) >= 12
    message = refusal("".join(chars[:12]).encode("gb18030"))
    assert "more than one" in message and "gb18030" in message and "cp949" in message


def _reads_as_hangul(raw: bytes) -> bool:
    try:
        text = raw.decode("cp949")
    except UnicodeDecodeError:
        return False
    return all("\uac00" <= c <= "\ud7a3" for c in text)


def test_utf8_results_are_unchanged_and_carry_no_encoding_note() -> None:
    text = "# 제목\n\n본문 한 줄\n\n中文段落\n"
    plain = parse(text.encode("utf-8"), "text/plain", ".txt")
    with_bom = parse(b"\xef\xbb\xbf" + text.encode("utf-8"), "text/plain", ".txt")
    expected = ["# 제목", "본문 한 줄", "中文段落"]
    assert [n.text for n in plain.nodes] == expected == [n.text for n in with_bom.nodes]
    assert all(not n.extraction_warnings for n in plain.nodes + with_bom.nodes)


@pytest.mark.parametrize("bom,encoding", [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")])
def test_a_utf16_byte_order_mark_is_followed(bom: bytes, encoding: str) -> None:
    decoded = decode_text(bom + SAMPLES["cp949"].encode(encoding))
    assert decoded.text == SAMPLES["cp949"] and decoded.encoding == "utf-16"


def test_declared_encoding_that_yields_control_characters_is_refused() -> None:
    raw = b'<html><head><meta charset="gb2312"></head><body><p>' + SAMPLES["gb18030"].encode(
        "gb18030"
    )
    with pytest.raises(ParserFailure) as caught:
        decode_text(raw + b"\x01</p></body></html>", declared="gb2312")
    assert caught.value.code is ParserErrorCode.TEXT_ENCODING_UNCONFIRMED
