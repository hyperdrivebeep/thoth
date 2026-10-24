from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.hypothesis_v5_wire import (
    ReceivedHypothesisPortfolioV5,
    ReceivedHypothesisTableFillV5,
)
from thoth.domain.language_validation import HypothesisLanguageCheck
from thoth.domain.research_followup import (
    CoverageMatrix,
    DecisionDelta,
    DecisionDeltaInput,
    ProjectReviewList,
    ProjectReviewListInput,
    UserProgressSummary,
)
from thoth.domain.research_history import (
    HistoricalResultInput,
    HistoricalResultView,
    HistoryDetail,
    HistoryDiff,
    HistoryItemInput,
    HistoryPage,
    HistoryTimelineInput,
)
from thoth.domain.research_request import (
    CurrentResultManifestV21,
    ThreadRequestRevision,
    ThreadRequestRevisionV21,
)
from thoth.domain.restore import (
    RestoreApplyInput,
    RestoreApplyResult,
    RestorePreview,
    RestorePreviewInput,
)
from thoth.domain.user_activity import UserActivityEvent
from thoth.protocol.jsonrpc import JsonRpcError, JsonRpcRequest, JsonRpcResponse

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "thread-request-v2.schema.json": ThreadRequestRevision,
    "thread-request-v21.schema.json": ThreadRequestRevisionV21,
    "hypothesis-portfolio-v4.schema.json": HypothesisPortfolioV4,
    "hypothesis-table-fill-v4.schema.json": HypothesisTableFillV4,
    "hypothesis-portfolio-v5.schema.json": ReceivedHypothesisPortfolioV5,
    "hypothesis-table-fill-v6.schema.json": ReceivedHypothesisTableFillV5,
    "hypothesis-language-check.schema.json": HypothesisLanguageCheck,
    "jsonrpc-error.schema.json": JsonRpcError,
    "jsonrpc-request.schema.json": JsonRpcRequest,
    "jsonrpc-response.schema.json": JsonRpcResponse,
    "history-timeline-input.schema.json": HistoryTimelineInput,
    "history-item-input.schema.json": HistoryItemInput,
    "history-page.schema.json": HistoryPage,
    "history-detail.schema.json": HistoryDetail,
    "historical-result-input.schema.json": HistoricalResultInput,
    "historical-result-view.schema.json": HistoricalResultView,
    "history-diff.schema.json": HistoryDiff,
    "restore-preview-input.schema.json": RestorePreviewInput,
    "restore-preview.schema.json": RestorePreview,
    "restore-apply-input.schema.json": RestoreApplyInput,
    "restore-apply-result.schema.json": RestoreApplyResult,
    "current-result-manifest-v21.schema.json": CurrentResultManifestV21,
    "user-activity-event-v1.schema.json": UserActivityEvent,
    "user-progress-summary.schema.json": UserProgressSummary,
    "coverage-matrix.schema.json": CoverageMatrix,
    "decision-delta-input.schema.json": DecisionDeltaInput,
    "decision-delta.schema.json": DecisionDelta,
    "project-review-list-input.schema.json": ProjectReviewListInput,
    "project-review-list.schema.json": ProjectReviewList,
}


def render_schema(model: type[BaseModel]) -> str:
    schema = model.model_json_schema(by_alias=True)
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export_schemas(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMA_MODELS.items():
        (destination / filename).write_text(render_schema(model), encoding="utf-8", newline="\n")
