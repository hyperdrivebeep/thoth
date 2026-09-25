"""Bounded receive counters for Responses SSE. Credentials are never stored."""

from dataclasses import dataclass, field
from time import monotonic

import httpx

from thoth.adapters.models.transport_diagnostic import (
    build_diagnostic,
    exception_detail,
    response_detail,
)
from thoth.domain.model_dispatch import (
    HttpRejectionMetadata,
    ModelReceiveObservation,
    PreparedModelDispatch,
    TransportDiagnostic,
    TransportTimeouts,
)


@dataclass
class ReceiveStats:
    received_bytes: int = 0
    visible_output_bytes: int = 0
    frame_counts: dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=lambda: monotonic())
    http_status: int | None = None
    response_id: str | None = None
    first_response_ms: int | None = None
    first_byte_ms: int | None = None
    last_event_type: str | None = None
    timeout_kind: str | None = None
    last_transport_phase: str | None = None
    local_cancel_requested: bool = False
    transport_closed: bool | None = None
    last_receive_at: float | None = None
    terminal_at: float | None = None
    diagnostic_fields: dict[str, object] = field(default_factory=dict, repr=False)
    diagnostic_unavailable: bool = False
    transport_diagnostic: TransportDiagnostic | None = None
    http_rejection: HttpRejectionMetadata | None = None

    def capture_response(self, response: httpx.Response) -> None:
        try:
            self.diagnostic_fields.update(response_detail(response))
        except Exception:
            self.diagnostic_unavailable = True

    def received_chunk(self, chunk: bytes) -> None:
        if chunk:
            try:
                self.last_receive_at = monotonic()
            except Exception:
                self.diagnostic_unavailable = True

    def capture_httpx_exception(self, error: httpx.HTTPError) -> None:
        # A response-context close can fail after the completion event. Retain its
        # primary exception detail without moving the already frozen receive time.
        self.transport_diagnostic = None
        try:
            self.diagnostic_fields.update(exception_detail(error))
        except Exception:
            self.diagnostic_unavailable = True

    def freeze_terminal(self) -> None:
        if self.transport_diagnostic is not None:
            return
        try:
            if self.terminal_at is None:
                self.terminal_at = monotonic()
            if self.last_receive_at is not None:
                self.diagnostic_fields["last_receive_gap_ms"] = round(
                    (self.terminal_at - self.last_receive_at) * 1000
                )
            self.transport_diagnostic = build_diagnostic(
                self.diagnostic_fields, self.diagnostic_unavailable
            )
        except Exception:
            self.transport_diagnostic = TransportDiagnostic(capture_state="UNAVAILABLE")

    def elapsed_ms(self) -> int:
        return round((monotonic() - self.started_at) * 1000)

    def snapshot(self, request: PreparedModelDispatch) -> ModelReceiveObservation:
        return ModelReceiveObservation(
            received_bytes=self.received_bytes,
            visible_output_bytes=self.visible_output_bytes,
            frame_counts=dict(self.frame_counts),
            max_stream_bytes=request.max_stream_bytes,
            max_visible_output_bytes=request.max_visible_output_bytes,
            max_frame_bytes=request.max_frame_bytes,
            timeout_ms=None
            if request.timeout_seconds is None
            else round(request.timeout_seconds * 1000),
            http_status=self.http_status,
            response_id=self.response_id,
            first_response_ms=self.first_response_ms,
            first_byte_ms=self.first_byte_ms,
            elapsed_ms=self.elapsed_ms(),
            last_event_type=self.last_event_type,
            timeout_kind=self.timeout_kind,
            last_transport_phase=self.last_transport_phase,
            local_cancel_requested=self.local_cancel_requested,
            transport_closed=self.transport_closed,
            transport_diagnostic=self.transport_diagnostic,
            transport_timeouts=(request.transport_timeouts or TransportTimeouts()).bounded(
                request.timeout_seconds
            ),
            http_rejection=self.http_rejection,
        )
