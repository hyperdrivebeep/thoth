"""What a failure log line says, and what it must leave out."""

import logging
import re
from pathlib import Path

import pytest

from thoth.domain.failure_text import (
    MESSAGE_LIMIT,
    REDACTED_INPUT,
    REDACTED_SECRET,
    BackgroundFailureLog,
    failure_text,
    input_texts,
)


def _raise_chain(detail: str) -> Exception:
    def inner() -> None:
        raise KeyError("portfolio")

    def outer() -> None:
        try:
            inner()
        except KeyError as exc:
            raise RuntimeError(detail) from exc

    try:
        outer()
    except RuntimeError as exc:
        return exc
    raise AssertionError("unreachable")


def test_every_exception_of_the_chain_is_named_with_the_frames_it_passed_through() -> None:
    text = failure_text(_raise_chain("could not build the result"))
    lines = text.splitlines()
    assert lines[0] == "RuntimeError: could not build the result"
    assert "caused by KeyError: 'portfolio'" in lines
    # A frame is "<file>:<line> in <function>".
    frames = [line for line in lines if line.startswith("  ")]
    assert any(re.fullmatch(r"  \S*test_failure_text\.py:\d+ in outer", line) for line in frames)
    assert any(re.fullmatch(r"  \S*test_failure_text\.py:\d+ in inner", line) for line in frames)
    # The path is one a report can carry: never the full path with the folders above the checkout.
    assert str(Path(__file__).resolve().parent).replace("\\", "/") not in text.replace("\\", "/")
    assert lines.index("caused by KeyError: 'portfolio'") > lines.index(lines[0])


def test_request_text_does_not_reach_the_log_but_identifiers_do() -> None:
    request_input = {
        "project_id": "project:lantern-study",
        "problem": "Why did LAB-42 fail? SECRET-MARK-123",
        "nested": {"api_key": "plain-key-value", "items": ["short", "another long value"]},
        "revision_digest": "a" * 64,
    }
    hidden = input_texts(request_input)
    assert "Why did LAB-42 fail? SECRET-MARK-123" in hidden
    assert "plain-key-value" in hidden and "another long value" in hidden
    # Short values and identifier keys stay readable: they are what a maintainer searches for.
    assert "short" not in hidden
    assert "project:lantern-study" not in hidden and "a" * 64 not in hidden
    assert list(hidden) == sorted(hidden, key=lambda text: (-len(text), text))

    exc = _raise_chain(
        "cannot process Why did LAB-42 fail? SECRET-MARK-123 for project:lantern-study"
    )
    text = failure_text(exc, redact=hidden)
    assert "SECRET-MARK-123" not in text
    assert f"cannot process {REDACTED_INPUT} for project:lantern-study" in text


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefgh12345678",
        "xai-ABCDEFGH12345678",
        "Bearer abcdefghijklmnop",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
    ],
)
def test_token_shaped_text_is_removed_from_a_message(secret: str) -> None:
    text = failure_text(ValueError(f"provider refused {secret} today"))
    assert secret not in text
    assert text == f"ValueError: provider refused {REDACTED_SECRET} today"


def test_a_long_message_is_cut_and_line_breaks_are_folded() -> None:
    text = failure_text(ValueError("first line\nsecond line " + "x" * 1000))
    message = text.removeprefix("ValueError: ")
    assert message.startswith("first line second line ")
    assert len(message) == MESSAGE_LIMIT + len("...")
    assert "\n" not in text


def test_an_exception_without_a_message_is_just_its_type() -> None:
    assert failure_text(TimeoutError()) == "TimeoutError"


def test_a_repeating_background_failure_is_logged_once_per_place(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = BackgroundFailureLog(logging.getLogger("thoth.test.background"))
    with caplog.at_level(logging.WARNING, logger="thoth.test.background"):
        log.note("index project_id=p", OSError("disk"))
        log.note("index project_id=p", OSError("disk"))  # the next tick, the same failure
        log.note("index project_id=q", OSError("disk"))  # another place
        log.note("index project_id=p", OSError("locked"))  # the failure changed
        log.clear("index project_id=p")
        log.note("index project_id=p", OSError("locked"))  # it worked in between, then failed again
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "background failure at index project_id=p\nOSError: disk",
        "background failure at index project_id=q\nOSError: disk",
        "background failure at index project_id=p\nOSError: locked",
        "background failure at index project_id=p\nOSError: locked",
    ]
    assert {record.levelno for record in caplog.records} == {logging.WARNING}
