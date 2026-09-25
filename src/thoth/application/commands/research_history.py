"""Query DTOs and protocol errors; no query creates an operation or journal."""

from pydantic import JsonValue

from thoth.application.services.historical_result import HistoricalResultReader
from thoth.application.services.research_history import ResearchHistoryService
from thoth.domain.research_history import (
    HistoricalResultInput,
    HistoryItemInput,
    HistoryScope,
    HistoryTimelineInput,
)
from thoth.domain.restore import RestoreError
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def history_rpc_error(exc: RestoreError) -> RpcApplicationError:
    return RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED, exc.reason_code, data={"reason_code": exc.reason_code}
    )


class ResearchHistoryHandlers:
    def __init__(self, history: ResearchHistoryService, results: HistoricalResultReader) -> None:
        self.history, self.results = history, results

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        try:
            if method == "revision/timeline/read":
                request = HistoryTimelineInput.model_validate(value)
                self.history.scopes.require(request.project_id, request.scope)
            elif method == "revision/timeline/item/read":
                detail = HistoryItemInput.model_validate(value)
                if detail.record_ref.project_id != detail.project_id:
                    raise RestoreError("HISTORY_SCOPE_MISMATCH")
                self.history.scopes.require(detail.project_id, detail.scope)
            else:
                result = HistoricalResultInput.model_validate(value)
                self.history.scopes.require(
                    result.project_id,
                    HistoryScope(
                        project_id=result.project_id,
                        thread_id=result.thread_id,
                        request_revision_digest=result.request_revision_digest,
                    ),
                )
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def timeline_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return self.history.list_timeline(
                HistoryTimelineInput.model_validate(value)
            ).model_dump(mode="json")
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def item_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return self.history.read_item(HistoryItemInput.model_validate(value)).model_dump(
                mode="json"
            )
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc

    async def result_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return self.results.read(HistoricalResultInput.model_validate(value)).model_dump(
                mode="json"
            )
        except RestoreError as exc:
            raise history_rpc_error(exc) from exc
