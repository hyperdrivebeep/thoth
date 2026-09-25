import pytest
from pydantic import ValidationError

from thoth.domain.base import DomainModel
from thoth.domain.research_failure import failure_cause


class _Strict(DomainModel):
    name: str


def test_failure_cause_keeps_pydantic_detail() -> None:
    with pytest.raises(ValidationError) as caught:
        _Strict.model_validate({"name": "ok", "extra": True})
    cause = failure_cause(caught.value, "RESEARCH_EXECUTION")
    assert cause.reason_code == "RESEARCH_INTERNAL_ERROR"
    assert cause.exception_type == "ValidationError"
    assert cause.detail is not None
    assert "extra" in cause.detail


def test_failure_cause_does_not_store_plain_error_text() -> None:
    cause = failure_cause(RuntimeError("sqlite exploded"), "RESEARCH_EXECUTION")
    assert cause.reason_code == "RESEARCH_INTERNAL_ERROR"
    assert cause.detail is None
