"""Durable bounded comparisons; neither scores nor offline receipts activate behavior."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import cast

from thoth.application.services.evaluation_inputs import EvaluationInputResolver
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import (
    EvaluationRunError,
    PairedRunRecord,
    sealed_payload,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.evaluation_runner import (
    EvaluationExecutorRegistryPort,
    EvaluationRunStorePort,
    EvaluationScoringPort,
)
from thoth.ports.ledger import LedgerPort
from thoth.ports.object_store import ObjectStorePort


class PairedEvaluationService:
    def __init__(
        self,
        *,
        inputs: EvaluationInputResolver,
        store: EvaluationRunStorePort,
        executors: EvaluationExecutorRegistryPort,
        scorer: EvaluationScoringPort,
        ledger: LedgerPort,
        objects: ObjectStorePort,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._inputs, self._store, self._executors = inputs, store, executors
        self._scorer, self._ledger, self._objects = scorer, ledger, objects
        self._fault = fault_injector

    def _checkpoint(self, name: str) -> None:
        if self._fault is not None:
            self._fault(name)

    def _set(self, previous: PairedRunRecord, **changes: object) -> PairedRunRecord:
        payload = previous.model_dump(mode="python", exclude={"record_digest"})
        payload.update(changes)
        payload["revision"] = previous.revision + 1
        record = PairedRunRecord.model_validate(
            sealed_payload("EVALUATION_RUN_RECORD", "record_digest", payload)
        )
        self._store.update(record, expected_revision=previous.revision)
        return record

    def _failure(
        self, record: PairedRunRecord, code: str, *, cancelled: bool = False
    ) -> PairedRunRecord:
        current = self._store.read(record.spec.project_id, record.spec.pair_id)
        if current is None:
            raise EvaluationRunError("EVALUATION_RECORD_MISSING")
        if current.state == "COMPLETE":
            return current
        if cancelled:
            return self._set(current, state="CANCELLED", reason_code=code)
        partial = code == "EVALUATION_INTERRUPTED" and current.active_arm is None
        return self._set(current, state="PARTIAL" if partial else "HELD", reason_code=code)

    async def run(self, project_id: str, plan: ControlRecord, binding_id: str) -> PairedRunRecord:
        proposed, binding, baseline_program, candidate_program = self._inputs.prepare(
            project_id, plan, binding_id
        )
        executor = self._executors.resolve(binding.executor_id)
        initial = PairedRunRecord.model_validate(
            sealed_payload(
                "EVALUATION_RUN_RECORD",
                "record_digest",
                {
                    "spec": proposed,
                    "state": "RUNNING",
                    "revision": 0,
                    "baseline": None,
                    "candidate": None,
                    "result": None,
                    "reason_code": None,
                    "active_arm": None,
                },
            )
        )
        record, claimed = self._store.claim(initial, reuse_limit=binding.case_pack.max_pair_runs)
        if not claimed:
            self._inputs.require_read(record.spec)
            if record.state == "COMPLETE":
                self._inputs.validate_current(record.spec, check_deadline=False)
                return self.read(project_id, record.spec.pair_id)
            if record.state != "PARTIAL" or record.active_arm is not None:
                raise EvaluationRunError("EVALUATION_IN_PROGRESS_OR_TERMINAL")
            self._inputs.validate_current(record.spec)
            record = self._set(record, state="RUNNING", reason_code=None)
        spec = record.spec
        public_inputs = tuple(case.public for case in binding.case_pack.cases)
        memory = cast(dict[str, object], binding.case_pack.initial_memory)
        try:
            if record.baseline is None:
                self._inputs.validate_current(spec)
                record = self._set(record, active_arm="BASELINE")
                baseline = await executor.execute(
                    spec, "BASELINE", baseline_program, public_inputs, memory
                )
                record = self._set(record, baseline=baseline, active_arm=None)
                if baseline.state == "CANCELLED":
                    raise asyncio.CancelledError()
                if baseline.state != "SUCCEEDED":
                    return self._failure(record, "EVALUATION_BASELINE_" + baseline.state)
                self._checkpoint("after_baseline_receipt")
            if record.candidate is None:
                self._inputs.validate_current(spec)
                record = self._set(record, active_arm="CANDIDATE")
                candidate = await executor.execute(
                    spec, "CANDIDATE", candidate_program, public_inputs, memory
                )
                record = self._set(record, candidate=candidate, active_arm=None)
                if candidate.state == "CANCELLED":
                    raise asyncio.CancelledError()
                self._checkpoint("after_candidate_receipt")
            assert record.baseline is not None and record.candidate is not None
            self._inputs.validate_current(spec)
            result = self._scorer.score(spec, binding, record.baseline, record.candidate)
            with self._ledger.transaction():
                self._inputs.validate_current(spec)
                record = self._set(record, state="COMPLETE", result=result, reason_code=None)
                self._checkpoint("after_final_result")
            return record
        except asyncio.CancelledError:
            self._failure(record, "EVALUATION_CANCELLED", cancelled=True)
            raise
        except EvaluationRunError as exc:
            return self._failure(record, exc.code)
        except ResourceScopeError as exc:
            self._failure(record, exc.code)
            raise
        except Exception:
            self._failure(record, "EVALUATION_INTERRUPTED")
            raise

    def read(self, project_id: str, pair_id: str) -> PairedRunRecord:
        record = self._store.read(project_id, pair_id)
        if record is None:
            raise EvaluationRunError("EVALUATION_RUN_NOT_FOUND")
        self._inputs.require_read(record.spec)
        for receipt in (record.baseline, record.candidate):
            if receipt is not None:
                raw = self._objects.read(receipt.output_blob_digest)
                if hashlib.sha256(raw).hexdigest() != receipt.output_blob_digest:
                    raise EvaluationRunError("EVALUATION_OUTPUT_DIGEST_MISMATCH")
        return record

    def require_assessment(self, project_id: str, pair_id: str) -> PairedRunRecord:
        record = self.read(project_id, pair_id)
        self._inputs.validate_current(record.spec, check_deadline=False)
        if record.state != "COMPLETE" or record.result is None:
            raise EvaluationRunError("EVALUATION_RESULT_NOT_COMPLETE")
        return record

    def require_current(self, record: PairedRunRecord) -> None:
        self._inputs.validate_current(record.spec, check_deadline=False)

    def require_policy_evidence(self, project_id: str, pair_id: str) -> PairedRunRecord:
        record = self.read(project_id, pair_id)
        self._inputs.validate_policy_evidence(record.spec)
        if record.state != "COMPLETE" or record.result is None or record.result.validity != "VALID":
            raise EvaluationRunError("BEHAVIOR_POLICY_COMPARISON_REQUIRED")
        return record

    def require_current_policy_evidence(self, record: PairedRunRecord) -> None:
        self._inputs.validate_policy_evidence(record.spec)

    def policy_basis(self, project_id: str, pair_id: str) -> PairedRunRecord:
        record = self._store.read(project_id, pair_id)
        if record is None or record.state != "COMPLETE" or record.result is None:
            raise EvaluationRunError("BEHAVIOR_POLICY_COMPARISON_REQUIRED")
        self._inputs.validate_policy_evidence(record.spec)
        return record
