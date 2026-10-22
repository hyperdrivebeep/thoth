"""Owner persistence on the caller's existing operation transaction."""

from sqlalchemy import Connection, insert, select

from thoth.adapters.storage.operation_owner_schema import operation_owners
from thoth.domain.operation import OperationOwnerRecord


def insert_owner(
    connection: Connection, operation_id: str, owner: OperationOwnerRecord | None
) -> None:
    if owner is not None and owner.host_token is not None:
        connection.execute(
            insert(operation_owners).values(operation_id=operation_id, **owner.model_dump())
        )


def read_owner(connection: Connection, operation_id: str) -> OperationOwnerRecord | None:
    row = (
        connection.execute(
            select(operation_owners).where(operation_owners.c.operation_id == operation_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return OperationOwnerRecord.model_validate(
        {key: row[key] for key in ("worker_id", "process_id", "process_marker", "host_token")}
    )
