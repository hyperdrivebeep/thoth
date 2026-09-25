"""One atomic owner for evaluation reservations, partial receipts and final results."""

from collections.abc import Mapping
from typing import cast

from sqlalchemy import Engine, func, insert, select, update

from thoth.adapters.storage.evaluation_run_schema import evaluation_runs
from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    read_connection,
    write_connection,
)
from thoth.domain.evaluation_run import EvaluationRunError, PairedRunRecord


class SqliteEvaluationRunStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _decode(row: Mapping[str, object]) -> PairedRunRecord:
        value = PairedRunRecord.model_validate_json(str(row["content_json"]))
        indexed = {
            "project_id": value.spec.project_id,
            "pair_id": value.spec.pair_id,
            "dedupe_digest": value.spec.dedupe_digest,
            "plan_id": value.spec.plan_id,
            "plan_digest": value.spec.plan_digest,
            "fixture_digest": value.spec.fixture_digest,
            "scorer_digest": value.spec.scorer_digest,
            "state": value.state,
            "revision": value.revision,
            "record_digest": value.record_digest,
        }
        if any(row[key] != child for key, child in indexed.items()):
            raise EvaluationRunError("EVALUATION_PROJECTION_MISMATCH")
        return value

    def claim(self, value: PairedRunRecord, *, reuse_limit: int) -> tuple[PairedRunRecord, bool]:
        value = PairedRunRecord.model_validate_json(value.model_dump_json())
        spec = value.spec
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            found = (
                connection.execute(
                    select(evaluation_runs).where(
                        evaluation_runs.c.project_id == spec.project_id,
                        evaluation_runs.c.dedupe_digest == spec.dedupe_digest,
                    )
                )
                .mappings()
                .first()
            )
            if found is not None:
                return self._decode(cast(Mapping[str, object], found)), False
            used = connection.execute(
                select(func.count())
                .select_from(evaluation_runs)
                .where(
                    evaluation_runs.c.project_id == spec.project_id,
                    evaluation_runs.c.fixture_digest == spec.fixture_digest,
                )
            ).scalar_one()
            if int(used) >= reuse_limit:
                raise EvaluationRunError("EVALUATION_EXPOSURE_REUSE_LIMIT")
            connection.execute(
                insert(evaluation_runs).values(
                    project_id=spec.project_id,
                    pair_id=spec.pair_id,
                    dedupe_digest=spec.dedupe_digest,
                    plan_id=spec.plan_id,
                    plan_digest=spec.plan_digest,
                    fixture_digest=spec.fixture_digest,
                    scorer_digest=spec.scorer_digest,
                    state=value.state,
                    revision=value.revision,
                    record_digest=value.record_digest,
                    content_json=value.model_dump_json(),
                )
            )
        return value, True

    def read(self, project_id: str, pair_id: str) -> PairedRunRecord | None:
        with read_connection(self._engine) as connection:
            raw = (
                connection.execute(
                    select(evaluation_runs).where(
                        evaluation_runs.c.project_id == project_id,
                        evaluation_runs.c.pair_id == pair_id,
                    )
                )
                .mappings()
                .first()
            )
        return None if raw is None else self._decode(cast(Mapping[str, object], raw))

    def read_plan(self, project_id: str, plan_id: str, plan_digest: str) -> PairedRunRecord | None:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(evaluation_runs).where(
                        evaluation_runs.c.project_id == project_id,
                        evaluation_runs.c.plan_id == plan_id,
                        evaluation_runs.c.plan_digest == plan_digest,
                    )
                )
                .mappings()
                .all()
            )
        if len(rows) > 1:
            raise EvaluationRunError("EVALUATION_PLAN_RUN_AMBIGUOUS")
        return None if not rows else self._decode(cast(Mapping[str, object], rows[0]))

    def update(self, value: PairedRunRecord, *, expected_revision: int) -> None:
        value = PairedRunRecord.model_validate_json(value.model_dump_json())
        if value.revision != expected_revision + 1:
            raise EvaluationRunError("EVALUATION_REVISION_INVALID")
        with write_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evaluation_runs).where(
                        evaluation_runs.c.project_id == value.spec.project_id,
                        evaluation_runs.c.pair_id == value.spec.pair_id,
                        evaluation_runs.c.revision == expected_revision,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise EvaluationRunError("EVALUATION_REVISION_CONFLICT")
            previous = self._decode(cast(Mapping[str, object], row))
            if previous.spec != value.spec or previous.state in {"COMPLETE", "HELD", "CANCELLED"}:
                raise EvaluationRunError("EVALUATION_IMMUTABLE_BASIS_CHANGED")
            if any(
                old is not None and old != new
                for old, new in (
                    (previous.baseline, value.baseline),
                    (previous.candidate, value.candidate),
                    (previous.result, value.result),
                )
            ):
                raise EvaluationRunError("EVALUATION_IMMUTABLE_RECEIPT_CHANGED")
            changed = connection.execute(
                update(evaluation_runs)
                .where(
                    evaluation_runs.c.project_id == value.spec.project_id,
                    evaluation_runs.c.pair_id == value.spec.pair_id,
                    evaluation_runs.c.dedupe_digest == value.spec.dedupe_digest,
                    evaluation_runs.c.revision == expected_revision,
                )
                .values(
                    state=value.state,
                    revision=value.revision,
                    record_digest=value.record_digest,
                    content_json=value.model_dump_json(),
                )
            ).rowcount
        if changed != 1:
            raise EvaluationRunError("EVALUATION_REVISION_CONFLICT")
