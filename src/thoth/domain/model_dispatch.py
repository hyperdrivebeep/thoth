"""Transport controls and observed limits, separate from scientific model output."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.model_settings import ResolvedModelSettings


class ModelControlCapability(DomainModel):
    capability_id: str
    version: str = "1.0.0"
    output_control: Literal[
        "SERVER_TOKENS", "CLIENT_VISIBLE_BYTES", "OBSERVATION_ONLY", "CONTROLLED", "UNVERIFIED"
    ]
    native_tools: Literal["NONE", "UNVERIFIED"]
    cancellation: Literal["LOCAL_TRANSPORT", "CONTROLLED", "UNVERIFIED"]
    owns_serialization: bool = False
    remote_stop_guaranteed: Literal[False] = False


class ModelDispatchRecord(DomainModel):
    dispatch_id: str
    thread_id: str | None = None
    operation_id: str | None = None
    capability: ModelControlCapability
    payload_bytes: int
    payload_digest: str
    output_reserved: int
    state: str = "RESERVED"
    received_bytes: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    remote_stop: str = "UNKNOWN"
    response_id: str | None = None
    model_settings: ResolvedModelSettings | None = None
    transport_observation: ModelReceiveObservation | None = None
    cached_input_tokens: int | None = None
    retry_of_dispatch_id: str | None = None
    transport_index: int = 0


@dataclass
class ModelCallContext:
    call_id: str
    dispatch_index: int = 0


class TransportTimeouts(DomainModel):
    """Optional internal controls; missing values inherit the persisted overall remainder."""

    connect_seconds: Decimal | None = Field(default=None, gt=0)
    read_idle_seconds: Decimal | None = Field(default=None, gt=0)
    write_seconds: Decimal | None = Field(default=None, gt=0)
    pool_seconds: Decimal | None = Field(default=None, gt=0)
    policy_ref: str | None = None

    def bounded(self, remaining: float | None) -> TransportTimeouts:
        if remaining is None:
            return self
        limit = Decimal(str(remaining))
        if limit <= 0:
            raise ValueError("TRANSPORT_REMAINING_NOT_POSITIVE")
        return self.model_copy(
            update={
                name: limit if (value := getattr(self, name)) is None else min(value, limit)
                for name in (
                    "connect_seconds",
                    "read_idle_seconds",
                    "write_seconds",
                    "pool_seconds",
                )
            }
        )


class TransportDiagnostic(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    capture_state: Literal["NOT_APPLICABLE", "CAPTURED", "PARTIAL", "UNAVAILABLE"] = (
        "NOT_APPLICABLE"
    )
    httpx_error_type: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    nested_cause_category: Literal[
        "NONE",
        "OS_ERROR",
        "SSL_ERROR",
        "NETWORK_ERROR",
        "PROTOCOL_ERROR",
        "DECODE_ERROR",
        "UNKNOWN",
    ] = "NONE"
    nested_errno: int | None = Field(default=None, ge=-(2**31), le=2**31 - 1, strict=True)
    cause_chain_state: Literal["NONE", "COMPLETE", "DEPTH_LIMIT", "CYCLE", "UNKNOWN"] = "NONE"
    http_version: Literal["HTTP/1.0", "HTTP/1.1", "HTTP/2", "UNKNOWN"] | None = None
    content_encoding: (
        Literal[
            "ABSENT", "IDENTITY", "GZIP", "DEFLATE", "BR", "ZSTD", "MULTIPLE", "OTHER", "UNKNOWN"
        ]
        | None
    ) = None
    body_framing: (
        Literal["CHUNKED", "CONTENT_LENGTH", "CONFLICT", "OTHER", "ABSENT", "UNKNOWN"] | None
    ) = None
    last_receive_gap_ms: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)


class HttpRejectionMetadata(DomainModel):
    """Safe HTTP-rejection classification. Raw bodies and secrets are not stored."""

    failure_category: Literal["HTTP_REJECTION"] = "HTTP_REJECTION"
    http_status: int = Field(ge=400, le=599, strict=True)
    rejection_kind: Literal["TRANSIENT_RATE_LIMIT", "ACCOUNT_LIMIT", "OTHER", "UNKNOWN"] = (
        "UNKNOWN"
    )
    classification_basis: str | None = Field(default=None, min_length=1, max_length=64)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400, strict=True)
    diagnostic_capture_state: Literal["CAPTURED", "PARTIAL", "UNAVAILABLE"] = "UNAVAILABLE"
    diagnostic_bytes: int | None = Field(default=None, ge=0, le=4096, strict=True)


class ModelReceiveObservation(DomainModel):
    received_bytes: int
    visible_output_bytes: int
    frame_counts: dict[str, int]
    max_stream_bytes: int | None
    max_visible_output_bytes: int | None
    timeout_ms: int | None
    http_status: int | None = None
    response_id: str | None = None
    first_response_ms: int | None = None
    first_byte_ms: int | None = None
    elapsed_ms: int | None = None
    last_event_type: str | None = None
    timeout_kind: str | None = None
    last_transport_phase: str | None = None
    local_cancel_requested: bool = False
    transport_closed: bool | None = None
    transport_timeouts: TransportTimeouts | None = None
    max_frame_bytes: int | None = None
    transport_diagnostic: TransportDiagnostic | None = None
    http_rejection: HttpRejectionMetadata | None = None


UNVERIFIED_MODEL_CONTROL = ModelControlCapability(
    capability_id="unregistered",
    output_control="UNVERIFIED",
    native_tools="UNVERIFIED",
    cancellation="UNVERIFIED",
)
CONTROLLED_MODEL_CONTROL = ModelControlCapability(
    capability_id="controlled-test-port",
    output_control="CONTROLLED",
    native_tools="NONE",
    cancellation="CONTROLLED",
)


@dataclass(frozen=True)
class PreparedModelDispatch:
    payload: bytes
    output_tokens_reserved: int
    max_visible_output_bytes: int | None
    max_stream_bytes: int | None
    timeout_seconds: float | None
    capability: ModelControlCapability
    transport_timeouts: TransportTimeouts | None = None
    max_frame_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class ModelTransportReply:
    text: str
    received_bytes: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    remote_stop: str = "UNKNOWN"
    response_id: str | None = None
    observation: ModelReceiveObservation | None = None
    cached_input_tokens: int | None = None


@dataclass(frozen=True)
class OAuthSession:
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)
    model: str
    reasoning_effort: str | None = None
