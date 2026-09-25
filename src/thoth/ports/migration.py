from __future__ import annotations

from typing import Protocol

from thoth.domain.migration import MigrationResult


class SchemaMigrationPort(Protocol):
    def upgrade_head(self) -> MigrationResult: ...
