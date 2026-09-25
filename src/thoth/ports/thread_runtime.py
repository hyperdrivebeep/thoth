from __future__ import annotations

from typing import Protocol

from thoth.domain.thread_runtime import ThreadActivity, ThreadCheckpoint, ThreadInputRecord


class ThreadRuntimeStorePort(Protocol):
    def append_activity(self, value: ThreadActivity) -> None: ...

    def list_activity(self, project_id: str, thread_id: str) -> tuple[ThreadActivity, ...]: ...

    def put_checkpoint(self, value: ThreadCheckpoint) -> None: ...

    def list_checkpoints(
        self, project_id: str, thread_id: str
    ) -> tuple[ThreadCheckpoint, ...]: ...

    def read_checkpoint(
        self, project_id: str, thread_id: str, checkpoint_id: str
    ) -> ThreadCheckpoint | None: ...

    def next_input_ordinal(self, thread_id: str) -> int: ...

    def enqueue_input(self, value: ThreadInputRecord) -> None: ...

    def list_pending_inputs(
        self, project_id: str, thread_id: str
    ) -> tuple[ThreadInputRecord, ...]: ...

    def mark_inputs_consumed(self, input_ids: tuple[str, ...]) -> None: ...
