"""Typed user-facing activity DTOs; raw execution traces stay outside this contract."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from thoth.domain.base import DomainModel


class UserActivityLocator(DomainModel):
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=120)
    cell: str | None = Field(default=None, max_length=64)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class UserActivityTarget(DomainModel):
    kind: Literal[
        "file",
        "uri",
        "artifact",
        "span",
        "table_cell",
        "line_range",
        "database_view",
        "sandbox",
        "model",
    ]
    title: str = Field(min_length=1, max_length=120)
    display_ref: str | None = Field(default=None, max_length=120)
    host_alias: str | None = Field(default=None, max_length=120)
    safe_uri: str | None = Field(default=None, max_length=500)
    locator: UserActivityLocator | None = None
    source_version_id: str | None = Field(default=None, max_length=80)
    hash_short: str | None = Field(default=None, min_length=8, max_length=16)
    currentness: Literal["current", "stale", "unknown_time", "access_unverified"] | None = None
    access_state: Literal["allowed", "blocked", "out_of_scope", "unknown"] = "unknown"


class UserActivityTool(DomainModel):
    display_name: str = Field(min_length=1, max_length=80)
    family: Literal["LOCAL", "GIT", "MCP", "POSTGRES", "S3", "SANDBOX", "MODEL"]
    operation: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    sanitized_args: dict[str, JsonValue] = Field(default_factory=dict)
    raw_command_available: Literal[False] = False
    command_detail: Literal["unavailable"] = "unavailable"


class UserActivityResult(DomainModel):
    summary_ko: str | None = Field(default=None, max_length=240)
    exit_code: int | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    reason_code: str | None = Field(
        default=None, max_length=160, pattern=r"^[A-Z][A-Z0-9_]{0,159}$"
    )


class UserActivityRedaction(DomainModel):
    applied: bool = False
    classes: tuple[str, ...] = ()
    public_note_ko: str | None = Field(default=None, max_length=160)
    source_content_included: Literal[False] = False


class UserActivityRefs(DomainModel):
    operation_alias: str | None = Field(default=None, max_length=80)
    receipt_ref: str | None = Field(default=None, max_length=80)
    source_ref: str | None = Field(default=None, max_length=80)
    revision_ref: str | None = Field(default=None, max_length=80)


class UserActivityEvent(DomainModel):
    schema_version: Literal["thoth.user_activity_event.v1"] = "thoth.user_activity_event.v1"
    event_id: str = Field(min_length=1, max_length=80)
    seq: int = Field(ge=1)
    time: str | None = Field(default=None, max_length=64)
    severity: Literal["info", "success", "warning", "error", "blocked", "input_required"]
    visibility: Literal["default", "expanded"] = "default"
    announce: Literal["none", "polite", "assertive"] = "none"
    phase: str | None = Field(default=None, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    activity_kind: Literal[
        "tool", "source", "model", "sandbox", "connector", "judgement", "control"
    ]
    action: Literal[
        "search",
        "open",
        "fetch",
        "parse",
        "read",
        "screen",
        "select_candidate",
        "adopt_support",
        "adopt_counter",
        "run",
        "call_model",
        "retry",
        "cancel",
        "hold",
        "stage_complete",
    ]
    label_ko: str = Field(min_length=1, max_length=240)
    why_ko: str | None = Field(default=None, max_length=320)
    state: Literal[
        "planned",
        "running",
        "succeeded",
        "failed",
        "blocked",
        "cancel_requested",
        "cancelled",
        "timed_out",
        "unknown_external_effect",
    ]
    research_relation: Literal[
        "none",
        "discovered",
        "opened",
        "fetched",
        "parsed",
        "read",
        "screened",
        "selected_candidate",
        "supports",
        "contradicts",
        "inconclusive",
        "held",
        "excluded",
        "stale",
        "access_blocked",
    ] = "none"
    target: UserActivityTarget | None = None
    tool: UserActivityTool | None = None
    result: UserActivityResult | None = None
    redaction: UserActivityRedaction = Field(default_factory=UserActivityRedaction)
    refs: UserActivityRefs = Field(default_factory=UserActivityRefs)
