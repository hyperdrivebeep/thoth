"""Short failure publication boundaries, entered only after external sandbox work."""

from collections.abc import Generator
from contextlib import contextmanager

from thoth.application.services.r2_test_lifecycle import PreparedResearchExecution
from thoth.application.services.sandbox_service import SandboxExecutionBundle, SandboxService
from thoth.domain.recovery import ExecutionFailureClass
from thoth.domain.sandbox import SandboxFailure, SandboxRunSpec
from thoth.ports.ledger import LedgerPort


class AdmissionRejected(Exception):
    """Only an acceptance failure; a later recovery-candidate error is never wrapped."""

    def __init__(self, failure: SandboxFailure) -> None:
        super().__init__(str(failure))
        self.failure = failure


class R2FailurePublication:
    def __init__(self, ledger: LedgerPort, sandbox: SandboxService) -> None:
        self._ledger = ledger
        self._sandbox = sandbox

    @contextmanager
    def transaction(self) -> Generator[None]:
        with self._ledger.transaction():
            yield

    @contextmanager
    def final_attempt(
        self,
        prepared: PreparedResearchExecution | None,
        failure_class: ExecutionFailureClass | None,
        retry_allowed: bool,
    ) -> Generator[None]:
        if prepared is not None and failure_class is not None and not retry_allowed:
            with self._ledger.transaction():
                yield
        else:
            # Plain execution, success and retry paths keep their existing boundaries.
            yield

    @contextmanager
    def accepting_failure(
        self, spec: SandboxRunSpec, bundle: SandboxExecutionBundle
    ) -> Generator[None]:
        try:
            with self._sandbox.accepting_observation(spec, bundle, defer_rejection=True):
                yield
        except SandboxFailure as failure:
            raise AdmissionRejected(failure) from failure

    def reject_admission(self, bundle: SandboxExecutionBundle) -> None:
        self._sandbox.reject_observation(bundle, reason="SANDBOX_ADMISSION_BASIS_CHANGED")
