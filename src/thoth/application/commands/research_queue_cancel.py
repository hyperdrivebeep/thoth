"""Record queued-input disposition after an operation cancellation."""

from pydantic import JsonValue

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_input_queue import ResearchInputQueue
from thoth.protocol.registry import CommandHandler


class QueueAwareOperationCancel:
    def __init__(
        self, original: CommandHandler, records: RequestRecords, queue: ResearchInputQueue
    ) -> None:
        self.original = original
        self.records = records
        self.queue = queue

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        owner = getattr(self.original, "__self__", None)
        authorize = getattr(owner, "authorize_before_claim", None)
        if callable(authorize):
            authorize(method, value)

    async def cancel(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        result = await self.original(value)
        if not isinstance(result, dict):
            raise ValueError("OPERATION_CANCEL_RESULT_INVALID")
        project_id, operation_id = value.get("project_id"), value.get("operation_id")
        if (
            result.get("cancelled") is True
            and isinstance(project_id, str)
            and isinstance(operation_id, str)
        ):
            with self.records.ledger.transaction():
                self.queue.cancelled(project_id, operation_id)
        return result
