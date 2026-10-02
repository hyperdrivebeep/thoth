"""A person's time or cost-effort estimate is added to an Action without touching the AI's."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from thoth.application.services.revision_service import CommitResult
from thoth.application.services.running_research import research_is_running
from thoth.domain.action import OrdinalEstimate
from thoth.domain.action_full import ActionRecord
from thoth.domain.base import DomainModel
from thoth.domain.effort_bands import EFFORT_BAND_PROFILE_ID, EFFORT_BAND_PROFILE_VERSION
from thoth.ports.operation import OperationStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

PATCHABLE_FIELDS = frozenset(
    {
        "primary_purpose",
        "secondary_purposes",
        "specification",
        "evidence_refs",
        "expected_observation_or_change",
    }
)


class HumanEffortEstimateInput(DomainModel):
    dimension: Literal["TIME", "COST_EFFORT"]
    band: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    basis_text: str = Field(default="", max_length=2_000)
    assumptions: tuple[str, ...] = ()


def require_no_research(operations: OperationStorePort | None, project_id: str) -> None:
    """A new Action revision adds a ledger head, which would branch a running investigation."""

    if operations is not None and research_is_running(operations, project_id):
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "ACTION_EDIT_RESEARCH_RUNNING")


@contextmanager
def estimate_transaction(
    atomic: Callable[[], AbstractContextManager[object]],
    operations: OperationStorePort | None,
    project_id: str,
    estimates: tuple[HumanEffortEstimateInput, ...],
) -> Generator[None]:
    """The save's own transaction; for a human estimate the check is repeated inside it."""

    with atomic():
        if estimates:
            require_no_research(operations, project_id)
        yield


class RevisionRequest(Protocol):
    patch: dict[str, JsonValue]
    human_effort_estimates: tuple[HumanEffortEstimateInput, ...]
    estimator_ref: str | None
    evidence_refs: tuple[str, ...]


class ActionRevisionService(Protocol):
    def atomic(self) -> AbstractContextManager[object]: ...

    def now(self) -> datetime: ...


def revise_action(
    revise: Callable[..., tuple[ActionRecord, CommitResult]],
    service: ActionRevisionService,
    operations: OperationStorePort | None,
    current: ActionRecord,
    request: RevisionRequest,
) -> tuple[ActionRecord, CommitResult]:
    """`action/revise`: checked updates, then the save inside one guarded transaction."""

    updates = revision_updates(
        current,
        request.patch,
        request.human_effort_estimates,
        request.estimator_ref,
        operations=operations,
        now=service.now(),
    )
    with estimate_transaction(
        service.atomic, operations, current.project_id, request.human_effort_estimates
    ):
        return revise(
            current,
            updates=updates,
            event_type="action/updated",
            evidence_refs=request.evidence_refs,
            # A person's time or effort estimate does not change what the action does.
            invalidate_downstream=bool(request.patch),
        )


def revision_updates(
    current: ActionRecord,
    patch: dict[str, JsonValue],
    estimates: tuple[HumanEffortEstimateInput, ...],
    estimator_ref: str | None,
    *,
    operations: OperationStorePort | None,
    now: datetime,
) -> dict[str, object]:
    """What `action/revise` changes: checked patch fields plus appended human estimates."""

    rejected = sorted(set(patch) - PATCHABLE_FIELDS)
    if rejected:
        raise RpcApplicationError(
            RpcErrorCode.DOMAIN_REJECTED, f"noncanonical Action patch: {', '.join(rejected)}"
        )
    if not patch and not estimates:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Action revision is empty")
    updates: dict[str, object] = {
        key: tuple(child) if key.endswith("_refs") and isinstance(child, list) else child
        for key, child in patch.items()
    }
    if estimates:
        require_no_research(operations, current.project_id)
        try:
            updates["generation_details"] = with_human_estimates(
                current, estimates, estimator_ref, now
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
    return updates


def with_human_estimates(
    current: ActionRecord,
    estimates: tuple[HumanEffortEstimateInput, ...],
    estimator_ref: str | None,
    now: datetime,
) -> dict[str, object]:
    """Generation details with the new HUMAN estimates appended. Raises ValueError on bad input."""

    details = current.generation_details
    if details is None:
        raise ValueError("Action has no generated details to estimate")
    if estimator_ref is None:
        raise ValueError("a human estimate requires estimator_ref")
    added = tuple(
        OrdinalEstimate(
            dimension=item.dimension,
            band=item.band,
            estimator_type="HUMAN",
            estimator_ref=estimator_ref,
            basis_text=item.basis_text.strip(),
            assumptions=item.assumptions,
            profile_id=EFFORT_BAND_PROFILE_ID,
            profile_version=EFFORT_BAND_PROFILE_VERSION,
            created_at=now,
        )
        for item in estimates
    )
    return details.model_copy(
        update={"effort_estimates": (*details.effort_estimates, *added)}
    ).model_dump(mode="python")
