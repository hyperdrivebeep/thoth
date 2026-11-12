"""The workspace log file of a running server."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from thoth.apps import workspace_guard
from thoth.apps.log_files import (
    LOG_BACKUPS,
    LOG_BYTES,
    close_workspace_log,
    install_workspace_log,
    workspace_log_path,
)


@pytest.fixture(autouse=True)
def no_leftover_handlers():
    yield
    close_workspace_log()


def _owned_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger("thoth").handlers
        if getattr(handler, "_thoth_workspace_log", False)
    ]


def test_warnings_of_any_thoth_logger_reach_the_workspace_file_in_utf8(tmp_path: Path) -> None:
    path = install_workspace_log(tmp_path)
    assert path == tmp_path / "logs" / "thoth.log" == workspace_log_path(tmp_path)
    assert path is not None
    logging.getLogger("thoth.internal_failure").error("internal command failure 한글 %s", "op:1")
    logging.getLogger("thoth.application.services.auto_index").warning("background failure")
    logging.getLogger("thoth.application.services.auto_index").info("not a problem")
    logging.getLogger("another.library").error("not ours")
    close_workspace_log()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("ERROR thoth.internal_failure internal command failure 한글 op:1")
    assert lines[1].endswith("WARNING thoth.application.services.auto_index background failure")


def test_the_file_is_bounded_and_installing_again_does_not_double_the_records(
    tmp_path: Path,
) -> None:
    install_workspace_log(tmp_path)
    path = install_workspace_log(tmp_path)
    assert path is not None
    files = [h for h in _owned_handlers() if isinstance(h, RotatingFileHandler)]
    assert len(files) == 1 and len(_owned_handlers()) == 2  # the file and the console
    assert (files[0].maxBytes, files[0].backupCount) == (LOG_BYTES, LOG_BACKUPS)
    logging.getLogger("thoth.x").warning("once")
    close_workspace_log()
    assert path.read_text(encoding="utf-8").count("once") == 1
    assert _owned_handlers() == []


def test_no_file_is_made_until_something_is_logged(tmp_path: Path) -> None:
    path = install_workspace_log(tmp_path)
    assert path is not None and not path.exists()


def test_an_exception_another_record_carries_is_written_cleaned_not_as_a_full_traceback(
    tmp_path: Path,
) -> None:
    path = install_workspace_log(tmp_path)
    assert path is not None
    try:
        raise RuntimeError("provider said no to sk-abcdefgh12345678 " + "x" * 1000)
    except RuntimeError as exc:
        # The way a failed research attempt is logged today.
        logging.getLogger("thoth.application.services.research_attempt_runner").error(
            "research attempt failed origin=%s", "MODEL_CALL", exc_info=exc
        )
    close_workspace_log()

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].endswith("research attempt failed origin=MODEL_CALL")
    assert lines[1].startswith("RuntimeError: provider said no to [secret] xxx")
    assert len(lines[1]) < 400 and "sk-abcdefgh12345678" not in text
    assert "Traceback (most recent call last)" not in text
    assert str(Path(__file__).resolve().parent) not in text
    assert any(line.startswith("  ") and " in test_an_exception" in line for line in lines)


def test_a_record_is_printed_once_even_when_a_library_set_up_a_root_console(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # caplog listens on the root logger, as the console handler of the migration tool does.
    install_workspace_log(tmp_path)
    logging.getLogger("thoth.internal_failure").error("only through the workspace handlers")
    assert caplog.records == []
    close_workspace_log()
    logging.getLogger("thoth.internal_failure").error("back to the usual route")
    assert [record.getMessage() for record in caplog.records] == ["back to the usual route"]


def test_a_log_folder_that_cannot_be_made_leaves_the_console_and_does_not_stop_the_server(
    tmp_path: Path,
) -> None:
    (tmp_path / "logs").write_text("a file where the folder should be", encoding="utf-8")
    assert install_workspace_log(tmp_path) is None
    assert len(_owned_handlers()) == 1
    assert not isinstance(_owned_handlers()[0], RotatingFileHandler)
    logging.getLogger("thoth.x").warning("still goes to the console")


def test_a_server_writes_to_the_log_of_the_workspace_it_owns_and_lets_go_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    with workspace_guard.owned_workspace(workspace, command="thoth serve"):
        logging.getLogger("thoth.internal_failure").error("while the server runs")
    logging.getLogger("thoth.internal_failure").error("after it stopped")

    text = workspace_log_path(workspace).read_text(encoding="utf-8")
    assert "while the server runs" in text and "after it stopped" not in text
    assert _owned_handlers() == []
