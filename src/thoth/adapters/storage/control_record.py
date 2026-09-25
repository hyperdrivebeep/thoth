from __future__ import annotations

from typing import cast

import orjson
from pydantic import ValidationError
from sqlalchemy import Engine, insert, select

from thoth.adapters.storage.schema import control_records
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.control_record import ControlRecord
from thoth.ports.control_record import ControlRecordStorePort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored control record is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteControlRecordStore(ControlRecordStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, value: ControlRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(control_records).values(
                    control_revision_id=value.control_revision_id,
                    project_id=value.project_id,
                    namespace=value.namespace,
                    record_type=value.record_type,
                    record_id=value.record_id,
                    version=value.version,
                    state=value.state,
                    content_json=dump(value.model_dump(mode="json")),
                    record_digest=value.record_digest,
                    supersedes_digest=value.supersedes_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(control_records)
                    .where(
                        control_records.c.project_id == project_id,
                        control_records.c.namespace == namespace,
                        control_records.c.record_id == record_id,
                    )
                    .order_by(control_records.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return None if row is None else ControlRecord.model_validate(load(row["content_json"]))

    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(control_records).where(
                        control_records.c.project_id == project_id,
                        control_records.c.record_digest == digest,
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else ControlRecord.model_validate(load(row["content_json"]))

    def list(
        self,
        project_id: str,
        namespace: str,
        record_type: str | None = None,
        *,
        latest_only: bool = True,
    ) -> tuple[ControlRecord, ...]:
        statement = select(control_records).where(
            control_records.c.project_id == project_id,
            control_records.c.namespace == namespace,
        )
        if record_type is not None:
            statement = statement.where(control_records.c.record_type == record_type)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(control_records.c.created_at, control_records.c.version)
            ).mappings()
            parsed: list[ControlRecord] = []
            for row in rows:
                try:
                    parsed.append(ControlRecord.model_validate(load(row["content_json"])))
                except ValidationError:
                    continue
            values = tuple(parsed)
        if not latest_only:
            return values
        latest: dict[str, ControlRecord] = {}
        for value in values:
            latest[value.record_id] = value
        return tuple(latest[key] for key in sorted(latest))
