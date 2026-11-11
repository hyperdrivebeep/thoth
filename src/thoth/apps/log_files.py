"""The workspace log of a running server: warnings and unexpected failures, kept for bug reports.

Every logger under "thoth" writes here from WARNING up, to <workspace>/logs/thoth.log (three old
files of about 1 MB are kept) and to the server's console. A record of a failed command comes from
thoth.protocol.failure_log and leaves out the request's text. An exception any other record
carries (a failed research attempt, a background check) is written the same way, by
thoth.domain.failure_text: types, cleaned and cut messages, short file paths. While the log is
installed the "thoth" loggers do not also hand their records to the root logger: a library that
configured a root console handler (the migration tool does) would print every record a second
time, with the full traceback.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from thoth.domain.failure_text import failure_text

LOG_DIRECTORY = "logs"
LOG_FILE = "thoth.log"
LOG_BYTES = 1_000_000
LOG_BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_LOGGER = "thoth"
_OWNED = "_thoth_workspace_log"
_ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]


class _FailureTextFormatter(logging.Formatter):
    """Writes an attached exception as failure_text does, not as a full traceback."""

    def formatException(self, ei: _ExcInfo) -> str:
        return "" if ei[1] is None else failure_text(ei[1])


def workspace_log_path(workspace: Path) -> Path:
    return workspace / LOG_DIRECTORY / LOG_FILE


def install_workspace_log(workspace: Path) -> Path | None:
    """Send the "thoth" loggers to the console and to the workspace log file.

    Returns the file, or None when its folder cannot be made (the console still gets the records).
    Calling it again replaces the handlers of the earlier call.
    """
    close_workspace_log()
    logger = logging.getLogger(_LOGGER)
    formatter = _FailureTextFormatter(_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    path = workspace_log_path(workspace)
    installed: Path | None = path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True
            )
        )
    except OSError:
        installed = None
    for handler in handlers:
        handler.setLevel(logging.WARNING)
        handler.setFormatter(formatter)
        setattr(handler, _OWNED, True)
        logger.addHandler(handler)
    logger.propagate = False
    return installed


def close_workspace_log() -> None:
    """Remove and close the handlers install_workspace_log added."""
    logger = logging.getLogger(_LOGGER)
    logger.propagate = True
    for handler in tuple(logger.handlers):
        if getattr(handler, _OWNED, False):
            logger.removeHandler(handler)
            handler.close()
