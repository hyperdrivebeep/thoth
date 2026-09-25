from __future__ import annotations

from typing import TypeVar, cast

import orjson
from sqlalchemy import Engine, Table, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.elements import ColumnElement

from thoth.adapters.storage.schema import (
    field_events,
    field_exports,
    field_protocol_seals,
    field_scores,
    field_session_metrics,
    field_sessions,
)
from thoth.domain.base import DomainModel
from thoth.domain.field_measurement import (
    FieldEventRecord,
    FieldExportBundle,
    FieldProtocolSeal,
    FieldScoreRecord,
    FieldSessionMetrics,
    FieldSessionRecord,
)
from thoth.ports.field_measurement import FieldMeasurementStorePort

TRecord = TypeVar("TRecord", bound=DomainModel)


def _dump(value: DomainModel) -> str:
    return orjson.dumps(value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS).decode()


class SqliteFieldMeasurementStore(FieldMeasurementStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def put_protocol(self, value: FieldProtocolSeal) -> None:
        self._insert(
            field_protocol_seals,
            protocol_id=value.protocol_id,
            project_id=value.project_id,
            protocol_digest=value.protocol_digest,
            content_json=_dump(value),
        )

    def read_protocol(self, project_id: str, digest: str) -> FieldProtocolSeal | None:
        return self._read_one(
            field_protocol_seals,
            (field_protocol_seals.c.project_id == project_id)
            & (field_protocol_seals.c.protocol_digest == digest),
            FieldProtocolSeal,
        )

    def list_protocols(self, project_id: str) -> tuple[FieldProtocolSeal, ...]:
        return self._list(
            field_protocol_seals,
            field_protocol_seals.c.project_id == project_id,
            FieldProtocolSeal,
        )

    def put_session(self, value: FieldSessionRecord) -> None:
        payload = {
            "session_id": value.session_id,
            "project_id": value.project_id,
            "protocol_digest": value.protocol_digest,
            "state": value.state,
            "content_json": _dump(value),
        }
        statement = sqlite_insert(field_sessions).values(**payload)
        with self._engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["session_id"],
                    set_={key: child for key, child in payload.items() if key != "session_id"},
                )
            )

    def read_session(self, project_id: str, session_id: str) -> FieldSessionRecord | None:
        return self._read_one(
            field_sessions,
            (field_sessions.c.project_id == project_id)
            & (field_sessions.c.session_id == session_id),
            FieldSessionRecord,
        )

    def list_sessions(
        self, project_id: str, protocol_digest: str
    ) -> tuple[FieldSessionRecord, ...]:
        return self._list(
            field_sessions,
            (field_sessions.c.project_id == project_id)
            & (field_sessions.c.protocol_digest == protocol_digest),
            FieldSessionRecord,
        )

    def put_event(self, value: FieldEventRecord) -> None:
        self._insert(
            field_events,
            event_id=value.event_id,
            project_id=value.project_id,
            session_id=value.session_id,
            event_type=value.event_type,
            content_json=_dump(value),
        )

    def list_events(
        self, project_id: str, session_id: str | None = None
    ) -> tuple[FieldEventRecord, ...]:
        condition = field_events.c.project_id == project_id
        if session_id is not None:
            condition &= field_events.c.session_id == session_id
        return self._list(field_events, condition, FieldEventRecord)

    def put_metrics(self, value: FieldSessionMetrics, project_id: str) -> None:
        self._insert(
            field_session_metrics,
            session_id=value.session_id,
            project_id=project_id,
            content_json=_dump(value),
        )

    def list_metrics(self, project_id: str) -> tuple[FieldSessionMetrics, ...]:
        return self._list(
            field_session_metrics,
            field_session_metrics.c.project_id == project_id,
            FieldSessionMetrics,
        )

    def put_score(self, value: FieldScoreRecord) -> None:
        self._insert(
            field_scores,
            score_id=value.score_id,
            project_id=value.project_id,
            session_id=value.session_id,
            content_json=_dump(value),
        )

    def list_scores(self, project_id: str) -> tuple[FieldScoreRecord, ...]:
        return self._list(
            field_scores,
            field_scores.c.project_id == project_id,
            FieldScoreRecord,
        )

    def put_export(self, value: FieldExportBundle) -> None:
        self._insert(
            field_exports,
            export_id=value.export_id,
            project_id=value.project_id,
            protocol_digest=value.protocol.protocol_digest,
            bundle_digest=value.bundle_digest,
            content_json=_dump(value),
        )

    def _insert(self, table: Table, **payload: object) -> None:
        with self._engine.begin() as connection:
            connection.execute(insert(table).values(**payload))

    def _read_one(
        self, table: Table, condition: ColumnElement[bool], model: type[TRecord]
    ) -> TRecord | None:
        values = self._list(table, condition, model)
        return None if not values else values[-1]

    def _list(
        self, table: Table, condition: ColumnElement[bool], model: type[TRecord]
    ) -> tuple[TRecord, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(table.c.content_json).where(condition)
            ).all()
        return tuple(
            model.model_validate(orjson.loads(str(cast(object, row[0])))) for row in rows
        )
