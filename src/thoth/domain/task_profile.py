"""Task-shape obligations are not measurement/evaluator Criterion profiles."""

from pydantic import Field

from thoth.domain.base import DomainModel


class TaskProfileRule(DomainModel):
    rule_id: str
    target: str
    question: str
    counterevidence_required: bool = False


class TaskProfileRecord(DomainModel):
    profile_ref: str
    version: int = Field(ge=1)
    rules: tuple[TaskProfileRule, ...]
    authority_ref: str
    enabled: bool = True
