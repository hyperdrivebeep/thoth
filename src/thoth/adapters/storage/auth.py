from __future__ import annotations

import orjson
from sqlalchemy import Engine, insert, select
from sqlalchemy.sql.elements import ColumnElement

from thoth.adapters.storage.schema import auth_sessions
from thoth.domain.auth import AuthSession
from thoth.ports.auth import AuthSessionStorePort


class SqliteAuthSessionStore(AuthSessionStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def put(self, value: AuthSession) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(auth_sessions).values(
                    session_id=value.session_id,
                    actor_id=value.actor_id,
                    project_id=value.project_id,
                    role_assignment_id=value.role_assignment_id,
                    token_digest=value.token_digest,
                    state=value.state,
                    content_json=orjson.dumps(
                        value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS
                    ).decode(),
                    expires_at=value.expires_at.isoformat(),
                )
            )

    def read_by_token_digest(self, token_digest: str) -> AuthSession | None:
        return self._read(auth_sessions.c.token_digest == token_digest)

    def read(self, session_id: str) -> AuthSession | None:
        return self._read(auth_sessions.c.session_id == session_id)

    def _read(self, condition: ColumnElement[bool]) -> AuthSession | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(auth_sessions.c.content_json).where(condition)
            ).first()
        return (
            None
            if row is None
            else AuthSession.model_validate(orjson.loads(str(row.content_json)))
        )
