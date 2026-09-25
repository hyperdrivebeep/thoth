from typing import Protocol

from thoth.domain.task_profile import TaskProfileRecord


class TaskProfileCatalogPort(Protocol):
    def profiles(self) -> tuple[TaskProfileRecord, ...]: ...
