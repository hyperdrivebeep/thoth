"""One log record for a command that ended in an unexpected exception.

The caller still seals the operation and answers "internal command failure" with the operation id;
this record is what lets a maintainer find the cause from that id. Typed refusals (application
errors, validation errors, permission errors) are answers, not failures, and are not logged here.
"""

from __future__ import annotations

import logging

from thoth.domain.failure_text import failure_text, input_texts
from thoth.domain.operation import OperationRecord
from thoth.protocol.jsonrpc import JsonRpcRequest

_LOG = logging.getLogger("thoth.internal_failure")


def log_internal_failure(
    request: JsonRpcRequest, operation: OperationRecord, exc: Exception
) -> None:
    try:
        detail = failure_text(exc, redact=input_texts(request.params.input))
    except Exception:
        # The failure is sealed by the caller either way; a broken description must not hide it.
        detail = f"{type(exc).__name__} (failure text unavailable)"
    _LOG.error(
        "internal command failure method=%s operation_id=%s project_id=%s\n%s",
        request.method,
        operation.operation_id,
        operation.project_id,
        detail,
    )
