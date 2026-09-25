"""Bounded read-only follow-up query handlers."""

from pydantic import JsonValue

from thoth.application.services.historical_result import HistoricalResultReader
from thoth.application.services.research_followup_projection import (
    ProjectReviewReader,
    decision_delta,
)
from thoth.application.services.research_history_scope import HistoryScopeValidator
from thoth.domain.research_followup import (
    DecisionDeltaInput,
    ProjectReviewListInput,
    ResultIdentity,
)
from thoth.domain.research_history import HistoricalResultInput, HistoryScope
from thoth.domain.restore import RestoreError
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def followup_rpc_error(exc: RestoreError) -> RpcApplicationError:
    return RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED, exc.reason_code, data={"reason_code": exc.reason_code}
    )


class ResearchFollowupHandlers:
    def __init__(
        self,
        *,
        results: HistoricalResultReader,
        reviews: ProjectReviewReader,
        scopes: HistoryScopeValidator,
    ) -> None:
        self.results = results
        self.reviews = reviews
        self.scopes = scopes

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        try:
            if method == "thread/result/compare/read":
                request = DecisionDeltaInput.model_validate(value)
                for identity in (request.before, request.after):
                    self.scopes.require(
                        request.project_id,
                        HistoryScope(
                            project_id=request.project_id,
                            thread_id=request.thread_id,
                            request_revision_digest=identity.request_revision_digest,
                        ),
                    )
            else:
                reviews = ProjectReviewListInput.model_validate(value)
                self.scopes.require(
                    reviews.project_id,
                    HistoryScope(project_id=reviews.project_id),
                )
        except RestoreError as exc:
            raise followup_rpc_error(exc) from exc

    async def compare_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            request = DecisionDeltaInput.model_validate(value)
            before = self._read_result(request.project_id, request.thread_id, request.before)
            after = self._read_result(request.project_id, request.thread_id, request.after)
            return decision_delta(
                project_id=request.project_id,
                thread_id=request.thread_id,
                before=request.before,
                after=request.after,
                before_manifest=before.manifest,
                after_manifest=after.manifest,
                before_currentness=before.basis_currentness.model_dump(mode="json"),
                after_currentness=after.basis_currentness.model_dump(mode="json"),
            ).model_dump(mode="json")
        except RestoreError as exc:
            raise followup_rpc_error(exc) from exc

    async def review_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return self.reviews.list(ProjectReviewListInput.model_validate(value)).model_dump(
                mode="json"
            )
        except RestoreError as exc:
            raise followup_rpc_error(exc) from exc

    def _read_result(self, project_id: str, thread_id: str, identity: ResultIdentity):
        return self.results.read(
            HistoricalResultInput(
                project_id=project_id,
                thread_id=thread_id,
                request_revision_digest=identity.request_revision_digest,
                result_revision_digest=identity.result_revision_digest,
            )
        )
