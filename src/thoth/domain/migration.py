from __future__ import annotations

from enum import StrEnum

from thoth.domain.base import DomainModel


class MigrationFailureCode(StrEnum):
    SCRIPT_HEAD_COUNT_INVALID = "MIGRATION_SCRIPT_HEAD_COUNT_INVALID"
    DATABASE_HEAD_COUNT_INVALID = "MIGRATION_DATABASE_HEAD_COUNT_INVALID"
    UPGRADE_FAILED = "MIGRATION_UPGRADE_FAILED"
    SCHEMA_NOT_CURRENT = "MIGRATION_SCHEMA_NOT_CURRENT"
    SCHEMA_NOT_READY = "MIGRATION_SCHEMA_NOT_READY"
    LEGACY_SCHEMA_UNRECOGNIZED = "MIGRATION_LEGACY_SCHEMA_UNRECOGNIZED"


class MigrationResult(DomainModel):
    database_path: str
    before_heads: tuple[str, ...]
    after_heads: tuple[str, ...]
    target_head: str
    upgraded: bool
    adopted_legacy_schema: bool = False


class MigrationFailure(RuntimeError):
    def __init__(self, code: MigrationFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
