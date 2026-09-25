from __future__ import annotations

from sqlalchemy import Engine, insert, select

from thoth.adapters.storage.test_validity_schema import research_test_assessments
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.test_validity import TestValidityAssessment
from thoth.ports.test_validity import TestValidityStorePort


class SqliteTestValidityStore(TestValidityStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_assessment(self, value: TestValidityAssessment) -> None:
        with write_connection(self._engine) as connection:
            existing = self.read_assessment(value.project_id, value.assessment_id)
            if existing is not None:
                if existing != value:
                    raise ValueError("TEST_ASSESSMENT_IDEMPOTENCY_CONFLICT")
                return
            connection.execute(
                insert(research_test_assessments).values(
                    project_id=value.project_id,
                    assessment_id=value.assessment_id,
                    prediction_id=value.prediction_id,
                    attempt_ref=value.attempt_ref,
                    assessment_digest=value.assessment_digest,
                    content_json=value.model_dump_json(),
                )
            )

    def read_assessment(self, project_id: str, assessment_id: str) -> TestValidityAssessment | None:
        with read_connection(self._engine) as connection:
            raw = connection.execute(
                select(research_test_assessments.c.content_json).where(
                    research_test_assessments.c.project_id == project_id,
                    research_test_assessments.c.assessment_id == assessment_id,
                )
            ).scalar_one_or_none()
        return None if raw is None else TestValidityAssessment.model_validate_json(str(raw))

    def find_attempt(
        self, project_id: str, prediction_id: str, attempt_ref: str
    ) -> TestValidityAssessment | None:
        with read_connection(self._engine) as connection:
            raw = connection.execute(
                select(research_test_assessments.c.content_json).where(
                    research_test_assessments.c.project_id == project_id,
                    research_test_assessments.c.prediction_id == prediction_id,
                    research_test_assessments.c.attempt_ref == attempt_ref,
                )
            ).scalar_one_or_none()
        return None if raw is None else TestValidityAssessment.model_validate_json(str(raw))
