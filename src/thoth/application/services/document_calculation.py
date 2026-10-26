"""Build arithmetic once for the selected source input, outside publication transactions."""

from thoth.application.services.numeric_series_observation import observe_numeric_series
from thoth.domain.derived_calculation import CheckedObservations
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.research_request import RevisionRef
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.document_calculator import DocumentCalculatorPort


class DocumentCalculationService:
    def __init__(self, calculator: DocumentCalculatorPort) -> None:
        self.calculator = calculator

    def observe(
        self,
        request: RevisionRef,
        evidence: tuple[EvidenceSpan, ...],
        artifacts: ArtifactLedgerPort,
    ) -> CheckedObservations | None:
        series, diagnostics = observe_numeric_series(request, evidence, artifacts, self.calculator)
        if not series and not diagnostics:
            return None
        return CheckedObservations(
            request_ref=request,
            series=series,
            calculations=tuple(c for s in series for c in self.calculator.calculate(s)),
            diagnostics=diagnostics,
        )
