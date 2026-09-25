import pytest
from pydantic import ValidationError

from thoth.domain.base import DomainModel
from thoth.domain.research_failure import failure_cause


class _Sample(DomainModel):
    name: str


def test_failure_cause_keeps_all_caps_codes() -> None:
    cause = failure_cause(RuntimeError("SOURCE_BASIS_CHANGED"), "RESEARCH_EXECUTION")
    assert cause.reason_code == "SOURCE_BASIS_CHANGED"
    assert cause.detail is None


def test_failure_cause_records_validation_detail() -> None:
    with pytest.raises(ValidationError) as caught:
        _Sample.model_validate({"name": 1})
    cause = failure_cause(caught.value, "RESEARCH_EXECUTION")
    assert cause.reason_code == "RESEARCH_INTERNAL_ERROR"
    assert cause.exception_type == "ValidationError"
    assert cause.detail is not None
    assert "name" in cause.detail
    assert "int_type" in cause.detail or "type" in cause.detail
