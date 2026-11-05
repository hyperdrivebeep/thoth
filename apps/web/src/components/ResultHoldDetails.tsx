import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { HistorySelection } from "../api/historyModels";
import { objectValue, stringValues } from "../api/presentation";
import type { CoverageMatrix, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { AnswerHold } from "./AnswerHold";
import { ConfirmedFacts } from "./ConfirmedFacts";
import { coverageCounts, selectedEvidenceCount } from "./coverageCounts";
import { Disclosure } from "./Disclosure";
import { counterSearchLine } from "./hypothesisView";
import { ReadableList } from "./ReadableList";
import { ResearchFollowupSummary } from "./ResearchFollowupSummary";
import type { ResearchDetail } from "./ResearchResultCard";
import { answerStatusLabel, nextActionLabel } from "./statusLabels";

/**
 * Why the answer is in its state: the conclusion lines, the review summary with its checks table, and the open
 * checks. One folding block under the answer; the header tags keep the counts visible when it is folded.
 */
export function ResultHoldDetails({ result, historySelection, coverageMatrix, progressSummary, nextUserAction, decisionDelta, onDetail }: {
  result: Record<string, unknown>; historySelection?: HistorySelection; coverageMatrix?: CoverageMatrix | null;
  progressSummary?: UserProgressSummary | null; nextUserAction?: NextUserAction | null; decisionDelta?: DecisionDelta | null;
  onDetail: (detail: ResearchDetail) => void;
}) {
  const status = typeof result.answer_status === "string" ? result.answer_status : null;
  const gaps = stringValues(objectValue(result.assessment).missing_items);
  const hasFollowup = Boolean(progressSummary || coverageMatrix || nextUserAction || decisionDelta);
  if (!status && !hasFollowup && gaps.length === 0) return null;
  const cite = (spanId: string, citations: Record<string, number>) => onDetail({ kind: "evidence", result, historySelection, focusSpanId: spanId, citations });
  const step = nextUserAction ?? progressSummary?.next_user_action;
  const counts = coverageCounts(coverageMatrix);
  const label = status ? message("holdDetails", answerStatusLabel(status) ?? status, counts.total) : t("result.holdDetailsPlain");
  // Open by default (the mock-up R1 shows it open); the header tags keep the counts visible once the reader folds it.
  return <Disclosure className="result-hold-details" label={label} defaultOpen>
    {status && <AnswerHold status={status} coverage={coverageMatrix} counter={counterSearchLine(result)}
      facts={<ConfirmedFacts result={result} onCite={cite}/>}
      next={step && step.action_type !== "NONE" ? nextActionLabel(step) : null}/>}
    <ResearchFollowupSummary answerEvidenceCount={selectedEvidenceCount(result)} progress={progressSummary} coverage={coverageMatrix} action={nextUserAction} delta={decisionDelta}
      onOpenEvidence={historySelection ? () => onDetail({ kind: "evidence", result, historySelection }) : undefined}/>
    {gaps.length > 0 && <div className="result-notice"><strong>{t("result.openChecks")}</strong><ReadableList items={gaps}/></div>}
  </Disclosure>;
}
