"""Current/query projection of immutable research-test assessment revisions."""

from sqlalchemy import Column, Index, String, Table, Text

from thoth.adapters.storage.schema import metadata

research_test_assessments = Table(
    "research_test_assessments",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("assessment_id", String(200), primary_key=True),
    Column("prediction_id", String(200), nullable=False),
    Column("attempt_ref", String(200), nullable=False),
    Column("assessment_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)
Index(
    "uq_research_test_prediction_attempt",
    research_test_assessments.c.project_id,
    research_test_assessments.c.prediction_id,
    research_test_assessments.c.attempt_ref,
    unique=True,
)
