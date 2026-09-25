from typing import TYPE_CHECKING

from pydantic import JsonValue

from thoth.domain.research_request import ResearchAttempt
from thoth.ports.command_authorization import CommandAuthorizerPort
from thoth.protocol.deferred import AcceptedRunning
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import CommandHandler, MethodRegistry

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


class ResearchOperationHandlers:
    def __init__(
        self, host: "ResearchThreadHandlers", pause: CommandHandler, resume: CommandHandler
    ) -> None:
        self.host, self.legacy_pause, self.legacy_resume = host, pause, resume

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        handler = self.legacy_pause if method == "operation/pause" else self.legacy_resume
        owner = getattr(handler, "__self__", None)
        if isinstance(owner, CommandAuthorizerPort):
            owner.authorize_before_claim(method, value)
        self.thread_scope(value)

    def thread_scope(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
        project_id, operation_id = str(value["project_id"]), str(value["operation_id"])
        attempt = self.host.records.journal_read(project_id, operation_id, ResearchAttempt)
        if attempt is None:
            return None
        scope: dict[str, JsonValue] = {
            "project_id": project_id,
            "thread_id": str(attempt.continuation["thread_id"]),
        }
        current = self.host.controls.current(scope)
        if current is None or current[1].operation_id != operation_id:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "REQUEST_SUPERSEDED")
        return scope

    async def pause(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        checked = await self.legacy_pause(value)
        scope = self.thread_scope(value)
        if scope is None:
            if not isinstance(checked, dict):
                raise ValueError("INVALID_LEGACY_OPERATION_CONTROL")
            return checked
        return await self.host.controls.pause(scope)

    async def resume(self, value: dict[str, JsonValue]) -> dict[str, JsonValue] | AcceptedRunning:
        checked = await self.legacy_resume(value)
        scope = self.thread_scope(value)
        if scope is None:
            if not isinstance(checked, dict):
                raise ValueError("INVALID_LEGACY_OPERATION_CONTROL")
            return checked
        return await self.host.controls.resume(scope)


def register_research_operations(registry: MethodRegistry, host: "ResearchThreadHandlers") -> None:
    handlers = ResearchOperationHandlers(
        host, registry.resolve("operation/pause"), registry.resolve("operation/resume")
    )
    registry.decorate("operation/pause", lambda _: handlers.pause)
    registry.decorate("operation/resume", lambda _: handlers.resume)
