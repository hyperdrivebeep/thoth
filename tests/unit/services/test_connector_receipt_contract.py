"""Observe receipt field evaluation and failure timing through the original root boundary."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from thoth.application.services.connector_service import ConnectorService
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorFetchResult,
    ConnectorReceipt,
    NativeVersion,
    NativeVersionKind,
)

OBSERVED = datetime(2026, 9, 1, tzinfo=UTC)
RECORDED = datetime(2026, 9, 1, 0, 0, 7, tzinfo=UTC)
RAW = b"\x00receipt\xff"


class Bytes(bytes):
    events: list[str]
    fault: RuntimeError | None

    def __new__(cls, value: bytes, events: list[str], fault: RuntimeError | None) -> "Bytes":
        result = super().__new__(cls, value)
        result.events, result.fault = events, fault
        return result

    def __len__(self) -> int:
        self.events.append("len")
        if self.fault is not None:
            raise self.fault
        return bytes.__len__(self)


class Clock:
    def __init__(self, events: list[str], fault: RuntimeError | None) -> None:
        self.events, self.fault = events, fault

    def now(self) -> datetime:
        self.events.append("clock")
        if self.fault is not None:
            raise self.fault
        return RECORDED


class Boundary(ConnectorService):
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def receipt(
        self,
        request: ConnectorAccessRequest,
        capability: ConnectorCapability,
        fetched: ConnectorFetchResult,
    ) -> ConnectorReceipt:
        return self._receipt(request=request, capability=capability, run_id="run", fetched=fetched)


@pytest.mark.parametrize("failure", [None, "dump", "len", "clock"])
def test_receipt_payload_digest_evaluation_order_and_failure_identity(
    failure: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    fault = RuntimeError("controlled receipt failure")
    request = ConnectorAccessRequest(
        actor_id="actor",
        project_id="p",
        connector_id="fixture",
        selector={"path": "data"},
        policy_id="policy",
        policy_revision=1,
        policy_digest="a" * 64,
    )
    capability = ConnectorCapability.model_construct(driver_version="driver:1")
    ref = ConnectorArtifactRef(
        source_uri="fixture://data",
        locator={"part": "one"},
        media_type="application/octet-stream",
        native_version=NativeVersion(kind=NativeVersionKind.ETAG, value="etag"),
        observed_at=OBSERVED,
    )
    fetched = ConnectorFetchResult(ref=ref, raw=RAW, content_sha256=hashlib.sha256(RAW).hexdigest())
    observed_raw = Bytes(RAW, events, fault if failure == "len" else None)
    fetched = fetched.model_copy(update={"raw": observed_raw})
    before_request = request.model_dump(mode="json")

    def observe[**P, R](call: Callable[P, R]) -> Callable[P, R]:
        def dump(*args: P.args, **kwargs: P.kwargs) -> R:
            events.append("dump")
            if failure == "dump":
                raise fault
            return call(*args, **kwargs)

        return dump

    h = Boundary(Clock(events, fault if failure == "clock" else None))
    with monkeypatch.context() as patch:
        patch.setattr(NativeVersion, "model_dump", observe(NativeVersion.model_dump))
        if failure is not None:
            with pytest.raises(RuntimeError) as caught:
                h.receipt(request, capability, fetched)
            assert caught.value is fault
            order = ["dump", "len", "clock"]
            assert events == order[: order.index(failure) + 1]
        else:
            receipt = h.receipt(request, capability, fetched)
            assert events == ["dump", "len", "clock"]
            expected: dict[str, object] = {
                "connector_run_id": "run",
                "project_id": "p",
                "connector_id": "fixture",
                "driver_version": "driver:1",
                "policy_digest": "a" * 64,
                "source_uri": "fixture://data",
                "native_version": {"kind": "ETAG", "value": "etag"},
                "content_sha256": hashlib.sha256(RAW).hexdigest(),
                "byte_size": 9,
                "recorded_at": RECORDED,
                "semantic_truth_certified": False,
            }
            expected["receipt_digest"] = domain_digest(
                "CONNECTOR_RECEIPT", "1.0.0", canonical_payload(expected)
            )
            assert receipt.model_dump(mode="python") == expected
    assert request.model_dump(mode="json") == before_request
    assert fetched.ref is ref and fetched.raw is observed_raw
