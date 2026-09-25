from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from thoth.domain.artifact import ParserSelection
from thoth.domain.base import DomainModel
from thoth.domain.canonical import model_digest
from thoth.domain.enums import AuthorityState, CutoffState, ParserErrorCode, SecurityClass
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.policy import PolicyDenialReceipt
from thoth.domain.web_acquisition import WebPage, WebTransformation


class ConnectorOperation(StrEnum):
    DISCOVER = "DISCOVER"
    READ = "READ"
    SUBSCRIBE = "SUBSCRIBE"


class NativeVersionKind(StrEnum):
    COMMIT = "COMMIT"
    VERSION_ID = "VERSION_ID"
    ETAG = "ETAG"
    SNAPSHOT = "SNAPSHOT"
    FILE_ID = "FILE_ID"
    CONTENT_HASH = "CONTENT_HASH"
    NONE = "NONE"


class ConnectorErrorCode(StrEnum):
    PARSER_FAILURE = "PARSER_FAILURE"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_CONFIGURATION_INVALID = "POLICY_CONFIGURATION_INVALID"
    POLICY_BINDING_MISMATCH = "POLICY_BINDING_MISMATCH"
    POLICY_DIGEST_MISMATCH = "POLICY_DIGEST_MISMATCH"
    POLICY_EMPTY_CONNECTOR_ALLOWLIST = "POLICY_EMPTY_CONNECTOR_ALLOWLIST"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    SCOPE_DENIED = "SCOPE_DENIED"
    SECURITY_CLASS_DENIED = "SECURITY_CLASS_DENIED"
    EGRESS_DENIED = "EGRESS_DENIED"
    CUTOFF_VIOLATION = "CUTOFF_VIOLATION"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_MUTATED = "SOURCE_MUTATED"
    UNSUPPORTED_VERSION_TOKEN = "UNSUPPORTED_VERSION_TOKEN"
    RATE_LIMITED = "RATE_LIMITED"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    PARTIAL_FETCH = "PARTIAL_FETCH"
    CONTENT_LIMIT_EXCEEDED = "CONTENT_LIMIT_EXCEEDED"
    CANCELLED = "CANCELLED"
    AMBIGUOUS_REMOTE_STATE = "AMBIGUOUS_REMOTE_STATE"
    DRIVER_ERROR = "DRIVER_ERROR"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    ROUTE_AMBIGUOUS = "ROUTE_AMBIGUOUS"


SelectorValueType = Literal["STRING", "INTEGER", "BOOLEAN"]


class SelectorFieldSpec(DomainModel):
    name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value_type: SelectorValueType
    required: bool = True

    def accepts(self, value: JsonValue) -> bool:
        if self.value_type == "STRING":
            return isinstance(value, str)
        if self.value_type == "INTEGER":
            return isinstance(value, int) and not isinstance(value, bool)
        return isinstance(value, bool)


class ConnectorSelectorContract(DomainModel):
    fields: tuple[SelectorFieldSpec, ...]
    allow_additional_fields: bool = False
    route_priority: int = Field(default=0, ge=-1_000, le=1_000)

    @model_validator(mode="after")
    def require_unambiguous_fields(self) -> ConnectorSelectorContract:
        names = tuple(field.name for field in self.fields)
        if not names or len(names) != len(set(names)):
            raise ValueError("connector selector fields must be non-empty and unique")
        if not any(field.required for field in self.fields):
            raise ValueError("connector selector contract requires a routing field")
        return self

    def accepts(self, selector: Mapping[str, JsonValue]) -> bool:
        fields = {field.name: field for field in self.fields}
        if any(field.required and field.name not in selector for field in self.fields):
            return False
        if not self.allow_additional_fields and any(key not in fields for key in selector):
            return False
        return all(fields[key].accepts(value) for key, value in selector.items() if key in fields)

    @property
    def contract_digest(self) -> Sha256:
        return model_digest(
            "CONNECTOR_SELECTOR_CONTRACT",
            self,
            schema_version="1.0.0",
        )


class ConnectorRouteDecision(DomainModel):
    connector_id: str = Field(min_length=1, max_length=160)
    selector_contract_digest: Sha256
    matched_fields: tuple[str, ...]


class ConnectorCapability(DomainModel):
    connector_id: str = Field(min_length=1, max_length=160)
    source_kind: str = Field(pattern=r"^(LOCAL|GIT|POSTGRES|S3|MCP|REST|CUSTOM)$")
    driver_version: str = Field(min_length=1, max_length=80)
    operations: tuple[ConnectorOperation, ...]
    auth_modes: tuple[str, ...] = ()
    native_version_kinds: tuple[NativeVersionKind, ...]
    checkpoint_kind: str = "NONE"
    egress_class: str = "NONE"
    write_supported: Literal[False] = False
    selector_contract: ConnectorSelectorContract

    @model_validator(mode="after")
    def require_read(self) -> ConnectorCapability:
        if ConnectorOperation.READ not in self.operations:
            raise ValueError("THOTH evidence connector must support READ")
        return self


class ConnectorAccessRequest(DomainModel):
    parser_selection: ParserSelection | None = None
    actor_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    connector_id: str = Field(min_length=1, max_length=160)
    operation: ConnectorOperation = ConnectorOperation.READ
    selector: dict[str, JsonValue]
    authority: AuthorityState = AuthorityState.UNCLASSIFIED
    cutoff_state: CutoffState = CutoffState.UNKNOWN_TIME
    security_class: SecurityClass = SecurityClass.INTERNAL
    query_security_class: SecurityClass | None = None
    cutoff_at: AwareDatetime | None = None
    max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=512 * 1024 * 1024)
    policy_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256


class NativeVersion(DomainModel):
    kind: NativeVersionKind
    value: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def require_consistent_value(self) -> NativeVersion:
        if self.kind == NativeVersionKind.NONE and self.value is not None:
            raise ValueError("NONE native version cannot carry a value")
        if self.kind != NativeVersionKind.NONE and not self.value:
            raise ValueError("native version value is required")
        return self


class ConnectorArtifactRef(DomainModel):
    source_uri: str = Field(min_length=1, max_length=2_000)
    locator: dict[str, JsonValue]
    media_type: str = Field(min_length=1, max_length=260)
    native_version: NativeVersion
    size_hint: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime


class ConnectorCheckpoint(DomainModel):
    kind: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4_000)
    digest: Sha256


class ConnectorFetchResult(DomainModel):
    ref: ConnectorArtifactRef
    raw: bytes
    content_sha256: Sha256
    checkpoint_after: ConnectorCheckpoint | None = None
    warnings: tuple[str, ...] = ()
    original_http: WebPage | None = None
    transformation: WebTransformation | None = None


class ConnectorRunRecord(DomainModel):
    connector_run_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    connector_id: str = Field(min_length=1, max_length=160)
    operation: ConnectorOperation
    state: str
    requested_scope_digest: Sha256
    policy_digest: Sha256
    artifact_refs: tuple[str, ...] = ()
    checkpoint_before: ConnectorCheckpoint | None = None
    checkpoint_after: ConnectorCheckpoint | None = None
    error_code: ConnectorErrorCode | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class ConnectorReceipt(DomainModel):
    connector_run_id: str
    project_id: ProjectId
    connector_id: str
    driver_version: str
    policy_digest: Sha256
    source_uri: str
    native_version: NativeVersion
    content_sha256: Sha256
    byte_size: int = Field(ge=0)
    recorded_at: AwareDatetime
    receipt_digest: Sha256
    semantic_truth_certified: Literal[False] = False


class ConnectorFailure(RuntimeError):
    def __init__(
        self,
        code: ConnectorErrorCode,
        message: str,
        *,
        policy_denial: PolicyDenialReceipt | None = None,
        parser_error: ParserErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.policy_denial = policy_denial
        self.parser_error = parser_error
