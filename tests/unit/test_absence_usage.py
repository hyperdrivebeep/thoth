"""Catalog batching is an authorized owner read, never a raised or suppressed quota."""

import pytest
from tests.unit.test_absence_claim_checks import NOW, proposal, scope, source, stage
from tests.unit.test_contextual_retrieval import work

from thoth.application.services.research_absence_checks import (
    assess_absence_claims,
    make_absence_scope,
)
from thoth.application.services.research_absence_usage import (
    ABSENCE_CATALOG_USE_LIMIT,
    ABSENCE_RESERVED_RESOURCE_USES,
    ABSENCE_SCOPE_USAGE_LIMIT,
    admit_absence_catalog,
)
from thoth.domain.absence_claim import AbsenceRun
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.resource_scope import (
    RESOURCE_SCOPE_USE_LIMIT,
    ResourceScopeError,
    current_resource_uses,
    record_resource_use,
    resource_use_scope,
)


class Catalog:
    def __init__(self, data: tuple[EvidenceSpan, ...], error: str | None = None) -> None:
        self.data, self.error = data, error
        self.calls = 0
        self.span_calls = 0

    def read_evidence_version(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> tuple[EvidenceSpan, ...]:
        self.calls += 1
        if self.error:
            raise ResourceScopeError(self.error)
        record_resource_use(project_id, artifact_id, "READ")
        return tuple(
            s
            for s in self.data
            if (s.project_id, s.artifact_id, s.source_version_id)
            == (project_id, artifact_id, source_version_id)
        )

    def read(self, ref: str) -> EvidenceSpan | None:
        self.span_calls += 1
        span = next((s for s in self.data if s.span_id == ref), None)
        if span:
            record_resource_use(span.project_id, ref, "READ")
        return span


def test_thousands_of_uniform_spans_use_one_owner_read() -> None:
    data = source("# Catalog\n\n" + "\n".join(f"record {i}" for i in range(4200)))
    reader = Catalog(data)
    state = AbsenceRun(scope(data, actual=False), data)
    with resource_use_scope(work().request_ref.project_id):
        admitted, reasons = admit_absence_catalog(state, data, reader.read, reader)
        uses = current_resource_uses()
        assert uses is not None and len(uses) == 1
        assert uses[0].resource_ref == data[0].artifact_id
        assert len(admitted) == len(data) and not reasons
    assert reader.calls == 1 and reader.span_calls == 0


def test_nonuniform_or_unsupported_catalog_falls_back_with_explicit_incomplete_scope() -> None:
    data = source("# Catalog\n\n" + "\n".join(f"record {i}" for i in range(1100)))
    reader = Catalog(data, "RESOURCE_CATALOG_SCOPE_NOT_UNIFORM")
    state = AbsenceRun(scope(data, actual=False), data)
    with resource_use_scope(work().request_ref.project_id):
        admitted, reasons = admit_absence_catalog(state, data, reader.read, reader)
        assert len(admitted) < len(data) and len(admitted) <= ABSENCE_CATALOG_USE_LIMIT
        assert ABSENCE_SCOPE_USAGE_LIMIT in reasons
        assert reader.span_calls == len(admitted)
        b = make_absence_scope(
            work().request_ref,
            "ALLOWED_EXTRACTED_AT_REQUEST",
            data,
            captured_at=NOW,
            phase="TEST",
            policy_ref="policy",
            cutoff_at=NOW,
            policy_digest="a" * 64,
            admitted_refs=admitted,
            reasons=reasons,
        )
        result = assess_absence_claims(
            work().request_ref,
            proposal("MISSING_LITERAL"),
            stage(),
            scope(data[:2], actual=True),
            data[:2],
            b,
            data,
        )
        assert not b.scan_complete and len(b.members) == len(data)
        assert result.items[0].b_result.outcome == "NOT_CHECKED"
        assert result.items[0].b_result.reason == ABSENCE_SCOPE_USAGE_LIMIT


def test_optional_scan_leaves_capacity_and_global_4096_guard_stays_exact() -> None:
    data = source()
    reader = Catalog(data)
    state = AbsenceRun(scope(data, actual=False), data)
    project = work().request_ref.project_id
    assert RESOURCE_SCOPE_USE_LIMIT == 4096
    with resource_use_scope(project):
        for index in range(RESOURCE_SCOPE_USE_LIMIT - ABSENCE_RESERVED_RESOURCE_USES):
            record_resource_use(project, f"existing:{index}", "READ")
        admitted, reasons = admit_absence_catalog(state, data, reader.read, reader)
        assert not admitted and ABSENCE_SCOPE_USAGE_LIMIT in reasons
        assert reader.calls == reader.span_calls == 0
        for index in range(ABSENCE_RESERVED_RESOURCE_USES):
            record_resource_use(project, f"later:{index}", "READ")
        assert len(current_resource_uses() or ()) == 4096
        with pytest.raises(ResourceScopeError, match="RESOURCE_SCOPE_USAGE_LIMIT"):
            record_resource_use(project, "one-too-many", "READ")


def test_catalog_permission_denial_is_not_a_missing_literal_or_quota_workaround() -> None:
    data = source()
    reader = Catalog(data, "RESOURCE_ACCESS_DENIED")
    state = AbsenceRun(scope(data, actual=False), data)
    with (
        resource_use_scope(work().request_ref.project_id),
        pytest.raises(ResourceScopeError, match="RESOURCE_ACCESS_DENIED"),
    ):
        admit_absence_catalog(state, data, reader.read, reader)
    assert reader.span_calls == 0
