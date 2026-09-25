from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[A-Fa-f0-9]{64}$")]

ProjectId = Identifier
ThreadId = Identifier
CycleId = Identifier
DecisionObjectId = Identifier
ArtifactId = Identifier
SourceVersionId = Identifier
EvidenceSpanId = Identifier
ClaimId = Identifier
CriterionId = Identifier
HypothesisId = Identifier
ActionId = Identifier
ExecutionId = Identifier
OutcomeId = Identifier
RevisionId = Identifier
ReceiptId = Identifier
OperationId = Identifier
CheckpointId = Identifier
