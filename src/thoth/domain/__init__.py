"""Framework-independent THOTH domain models."""

from thoth.domain.action import ActionCandidate, ActionPlan, DecisionAnalysis
from thoth.domain.artifact import ArtifactEnvelope, SourceLocator, StructuralDocument
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.project import Project, WorkThread
from thoth.domain.receipt import Receipt
from thoth.domain.revision import RevisionChangeSet, SemanticRevision

__all__ = [
    "ActionCandidate",
    "ActionPlan",
    "ArtifactEnvelope",
    "CriterionCandidate",
    "DecisionAnalysis",
    "EvidenceSpan",
    "Hypothesis",
    "HypothesisPortfolio",
    "InformationSufficiencyAssessment",
    "Project",
    "Receipt",
    "RevisionChangeSet",
    "SemanticRevision",
    "SourceLocator",
    "StructuralDocument",
    "WorkThread",
]
