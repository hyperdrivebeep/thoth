from __future__ import annotations

import orjson
from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import tui_sessions
from thoth.adapters.storage.transaction import write_connection
from thoth.domain.conversation import TuiSessionState
from thoth.ports.conversation import ConversationSessionStorePort


class SqliteConversationSessionStore(ConversationSessionStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read(self, session_id: str) -> TuiSessionState | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(tui_sessions.c.content_json).where(tui_sessions.c.session_id == session_id)
            ).first()
        return None if row is None else TuiSessionState.model_validate(orjson.loads(str(row[0])))

    def put(self, value: TuiSessionState) -> None:
        payload = {
            "session_id": value.session_id,
            "revision": value.revision,
            "content_json": orjson.dumps(
                value.model_dump(mode="json"),
                option=orjson.OPT_SORT_KEYS,
            ).decode(),
        }
        statement = sqlite_insert(tui_sessions).values(**payload)
        with write_connection(self._engine) as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["session_id"],
                    set_={"revision": value.revision, "content_json": payload["content_json"]},
                )
            )
