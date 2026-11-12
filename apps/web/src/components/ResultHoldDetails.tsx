import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { useResultLanguage } from "../i18n/ResponseLanguageContext";
import type { HistorySelection } from "../api/historyModels";
import { objectValue, stringValues } from "../api/presentation";
import type { CoverageMatrix, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { AnswerHold } from "./AnswerHold";
import { ConfirmedFacts } from "./ConfirmedFacts";
import { coverageCounts, selectedEvidenceCount } from "./coverageCounts";
import { Disclosure } from "./Disclosure";
import { foldedOriginals } from "./foldedOriginals";
import { followupTechnical } from "./followupItems";
import { ReadableList } from "./ReadableList";
import { ResearchFollowupSummary } from "./ResearchFollowupSummary";
import { TechnicalDetails, type TechnicalGroup } from "./TechnicalDetails";
import type { ResearchDetail } from "./ResearchResultCard";

/**
 * Why the answer is in its state: the review summary with its checks table, what the assessment did not
 * check, and one folded block of technical details for all of it. One folding block under the answer; the
 * header tags keep the status and the counts visible when it is folded, so they are not repeated inside.
 */
export function ResultHoldDetails({ result, historySelection, coverageMatrix, progressSummary, nextUserAction, decisionDelta, earlierRound, onDetail }: {
  result: Record<string, unknown>; historySelection?: HistorySelection; coverageMatrix?: CoverageMatrix | null;
  progressSummary?: UserProgressSummary | null; nextUserAction?: NextUserAction | null; decisionDelta?: DecisionDelta | null;
  /** A saved round before the latest one: its answer checks are not sent, and never will be. */
  earlierRound?: boolean;
  onDetail: (detail: ResearchDetail) => void;
}) {
  const language = useResultLanguage();
  const status = typeof result.answer_status === "string" ? result.answer_status : null;
  const gaps = stringValues(objectValue(result.assessment).missing_items);
  const hasFollowup = Boolean(progressSummary || coverageMatrix || nextUserAction || decisionDelta);
  if (!status && !hasFollowup && gaps.length === 0) return null;
  const cite = (spanId: string, citations: Record<string, number>) => onDetail({ kind: "evidence", result, historySelection, focusSpanId: spanId, citations });
  const counts = coverageCounts(coverageMatrix);
  const label = status ? message("holdDetails", counts.total) : t("result.holdDetailsPlain");
  const technical: TechnicalGroup[] = [
    ...followupTechnical({ progress: progressSummary, coverage: coverageMatrix, action: nextUserAction }, language),
    { label: t("result.openChecks"), items: foldedOriginals(gaps, language) },
  ];
  // Open by default (the mock-up R1 shows it open); the header tags keep the counts visible once the reader folds it.
  return <Disclosure className="result-hold-details" label={label} defaultOpen>
    {status && <AnswerHold status={status} coverage={coverageMatrix} earlierRound={earlierRound} facts={<ConfirmedFacts result={result} onCite={cite}/>}/>}
    <ResearchFollowupSummary answerEvidenceCount={selectedEvidenceCount(result)} progress={progressSummary} coverage={coverageMatrix} action={nextUserAction} delta={decisionDelta}
      onOpenEvidence={historySelection ? () => onDetail({ kind: "evidence", result, historySelection }) : undefined}/>
    {gaps.length > 0 && (
      <div className="result-notice">
        <strong>{t("result.openChecks")}</strong>
        <p className="muted">{t("result.openChecksNote")}</p>
        <ReadableList items={gaps} technical={false} />
      </div>
    )}
    <TechnicalDetails groups={technical} />
  </Disclosure>;
}
