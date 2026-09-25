from __future__ import annotations

from typing import Protocol

from thoth.domain.evaluator import EvaluatorCapability
from thoth.ports.improvement import ImprovementEvaluatorPort


class EvaluatorRegistryPort(ImprovementEvaluatorPort, Protocol):
    def capabilities(self) -> tuple[EvaluatorCapability, ...]: ...

