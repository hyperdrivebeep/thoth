"""Hold HOSTED_REVIEW research until the durable snapshot is committed.

Container disk is ephemeral. The Worker must persist the accepted operation to R2
before OpenAI is called. Local `create_runtime` tests leave this gate disabled.
"""

from __future__ import annotations

import asyncio

from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

SNAPSHOT_UNCOMMITTED = "HOSTED_REVIEW_SNAPSHOT_UNCOMMITTED"


class HostedDispatchCoordinator:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.enabled = False
        self._events: dict[str, asyncio.Event] = {}
        self._released: set[str] = set()
        self._aborted: set[str] = set()

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False
        self._released.clear()
        self._aborted.clear()
        for event in self._events.values():
            event.set()
        self._events.clear()

    def release(self, operation_id: str) -> None:
        identifier = operation_id.strip()
        if not identifier:
            raise ValueError("HOSTED_REVIEW_DISPATCH_RELEASE_INVALID")
        self._released.add(identifier)
        self._events.setdefault(identifier, asyncio.Event()).set()

    def abort(self, operation_id: str) -> None:
        identifier = operation_id.strip()
        if not identifier:
            raise ValueError("HOSTED_REVIEW_DISPATCH_ABORT_INVALID")
        self._aborted.add(identifier)
        self._events.setdefault(identifier, asyncio.Event()).set()

    async def wait(self, operation_id: str) -> bool:
        if not self.enabled:
            return True
        if operation_id in self._aborted:
            return False
        if operation_id in self._released:
            return True
        event = self._events.setdefault(operation_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=self.timeout_seconds)
        except TimeoutError:
            return False
        if operation_id in self._aborted:
            return False
        return operation_id in self._released


hosted_dispatch = HostedDispatchCoordinator()


async def wait_for_hosted_dispatch(operation_id: str) -> None:
    if await hosted_dispatch.wait(operation_id):
        return
    raise RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED,
        SNAPSHOT_UNCOMMITTED,
        data={
            "reason_code": SNAPSHOT_UNCOMMITTED,
            "remote_observation": "NOT_SENT",
            "pre_io": True,
        },
    )
