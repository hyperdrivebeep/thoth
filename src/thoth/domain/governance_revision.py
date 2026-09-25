"""Version-one immutable snapshots of the three mutable governance projections."""

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest

GovernanceKind = Literal["PROJECT", "ROLE", "SOURCE_BINDING"]
Projection = dict[str, JsonValue]


class GovernanceRevisionBody(DomainModel):
    project_id: str
    record_kind: GovernanceKind
    record_id: str
    revision: int = Field(ge=1)
    previous_digest: str | None = None
    origin: Literal["RECORDED", "LEGACY_BASELINE"]
    projection: Projection
    related_refs: dict[str, str] = Field(default_factory=dict)
    actor_ref: str
    recorded_at: AwareDatetime
    schema_version: Literal["1.0.0"] = "1.0.0"

    @model_validator(mode="after")
    def validate_identity(self) -> "GovernanceRevisionBody":
        key = {
            "PROJECT": "project_id",
            "ROLE": "role_assignment_id",
            "SOURCE_BINDING": "binding_id",
        }[self.record_kind]
        if (
            self.projection.get(key) != self.record_id
            or self.projection.get("project_id") != self.project_id
        ):
            raise ValueError("GOVERNANCE_RECORD_IDENTITY_MISMATCH")
        if self.revision == 1 and self.previous_digest is not None:
            raise ValueError("GOVERNANCE_INITIAL_PREDECESSOR_FORBIDDEN")
        if self.revision > 1 and not self.previous_digest:
            raise ValueError("GOVERNANCE_PREDECESSOR_REQUIRED")
        if self.origin == "LEGACY_BASELINE" and self.revision != 1:
            raise ValueError("GOVERNANCE_LEGACY_BASELINE_MUST_BE_INITIAL")
        return self


class GovernanceRevision(GovernanceRevisionBody):
    record_digest: str

    @model_validator(mode="after")
    def validate_digest(self) -> "GovernanceRevision":
        body = GovernanceRevisionBody.model_validate(self.model_dump(exclude={"record_digest"}))
        if self.record_digest != domain_digest(
            "PROJECT_GOVERNANCE_REVISION", "1.0.0", canonical_payload(body)
        ):
            raise ValueError("GOVERNANCE_REVISION_DIGEST_MISMATCH")
        return self


class GovernanceReceiptBody(DomainModel):
    project_id: str
    record_kind: GovernanceKind
    record_id: str
    before_digest: str | None
    after_digest: str
    origin: Literal["RECORDED", "LEGACY_BASELINE"]
    actor_ref: str
    recorded_at: AwareDatetime
    schema_version: Literal["1.0.0"] = "1.0.0"


class GovernanceReceipt(GovernanceReceiptBody):
    receipt_digest: str

    @model_validator(mode="after")
    def validate_digest(self) -> "GovernanceReceipt":
        body = GovernanceReceiptBody.model_validate(self.model_dump(exclude={"receipt_digest"}))
        if self.receipt_digest != domain_digest(
            "PROJECT_GOVERNANCE_RECEIPT", "1.0.0", canonical_payload(body)
        ):
            raise ValueError("GOVERNANCE_RECEIPT_DIGEST_MISMATCH")
        return self


def seal_governance(body: GovernanceRevisionBody) -> tuple[GovernanceRevision, GovernanceReceipt]:
    revision = GovernanceRevision.model_validate(
        {
            **body.model_dump(),
            "record_digest": domain_digest(
                "PROJECT_GOVERNANCE_REVISION", "1.0.0", canonical_payload(body)
            ),
        }
    )
    receipt = GovernanceReceiptBody(
        project_id=body.project_id,
        record_kind=body.record_kind,
        record_id=body.record_id,
        before_digest=body.previous_digest,
        after_digest=revision.record_digest,
        origin=body.origin,
        actor_ref=body.actor_ref,
        recorded_at=body.recorded_at,
    )
    return revision, GovernanceReceipt.model_validate(
        {
            **receipt.model_dump(),
            "receipt_digest": domain_digest(
                "PROJECT_GOVERNANCE_RECEIPT", "1.0.0", canonical_payload(receipt)
            ),
        }
    )
