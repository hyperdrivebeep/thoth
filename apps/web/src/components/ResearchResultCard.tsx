import { ResponseLanguageContext, resultLanguage } from "../i18n/ResponseLanguageContext";
import { t, useTranslation } from "../i18n/locale";
import { Button } from "@blueprintjs/core";

export type DetailKind = "evidence" | "hypotheses" | "reference" | "actions" | "map" | "outcome" | "memory" | "history" | "diagnostics";
export type ResearchDetail = { kind: DetailKind; result: Record<string, unknown>; historySelection?: HistorySelection;
  /** Source to scroll to and highlight, and the numbers the answer gave each source. */
  focusSpanId?: string; citations?: Record<string, number> };
import { objectValue, objectList, textValue } from "../api/presentation";
import type { ExecutionError, ResearchFailure } from "../api/research";
import type { Currentness, HistorySelection } from "../api/historyModels";
import type { CoverageMatrix, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { currentnessLabel, currentnessReasonNote } from "./history/historyPresentation";
import { Disclosure } from "./Disclosure";
import { AnswerBody } from "./AnswerBody";
import { CheckedObservations } from "./CheckedObservations";
import { AbsenceChecks } from "./AbsenceChecks";
import { TraceOriginLine } from "./TraceOriginLine";
import { HypothesisLinkNotice } from "./HypothesisLinkNotice";
import type { HypothesisLink } from "./hypothesisLinkText";
import type { ReasonDistribution } from "../api/hypothesisLink";
import { causeUnconfirmed } from "./hypothesisReview";
import { CauseUnconfirmed } from "./HypothesisFields";
import { ExecutionHoldNotice } from "./ExecutionHoldNotice";
import { GroundingRowsNotice } from "./GroundingNotice";
import type { ExecutionHold } from "../api/research";
import { interruptionView } from "./resultInterruption";
import { ResultInterruptionNotice } from "./ResultInterruptionNotice";
import { ResultStatusStrip } from "./ResultStatusStrip";
import { ResultHoldDetails } from "./ResultHoldDetails";

/**
 * One research result, read top to bottom: state tags next to the heading, the answer, the detail buttons, then why
 * the answer is in that state (folding), the alternative explanations and the next actions.
 */
export function ResearchResultCard({ result, state, unavailable, error, failure, terminalReason, onDetail, onRetry, onResume, resumeCompleted, resuming, historySelection, currentness, progressSummary, coverageMatrix, nextUserAction, decisionDelta, usageLine, executionHold, projectId, holdCauseRef, onOpenTrace, hypothesisLinks, linkDistribution, hasSavedResult = false }: {
  result: Record<string, unknown> | null; state: string; unavailable?: string; error?:ExecutionError | null; failure?:ResearchFailure | null; terminalReason?:string | null; onDetail: (detail: ResearchDetail) => void; onRetry?: () => void;
  /** Continue the interrupted run from where it stopped; only offered with the number of stages it had finished. */
  onResume?: () => void; resumeCompleted?: number; resuming?: boolean; historySelection?: HistorySelection; currentness?: Currentness;
  progressSummary?: UserProgressSummary | null; coverageMatrix?: CoverageMatrix | null; nextUserAction?: NextUserAction | null; decisionDelta?: DecisionDelta | null; usageLine?: string;
  /** A held closed-loop test of this result, with the project and the revision that clearing it is recorded against. */
  executionHold?: ExecutionHold | null; projectId?: string; holdCauseRef?: string;
  /** Back to the trace row this investigation started from. */
  onOpenTrace?: (row: { kind: string; id: string }) => void;
  /** Which of this answer's hypotheses came from a trace row and whether that row's verdict has changed since. */
  hypothesisLinks?: HypothesisLink[]; linkDistribution?: ReasonDistribution;
  hasSavedResult?: boolean;
}) {
  useTranslation();
  const languageCheck = objectValue(result?.hypothesis_language_check);
  const languageFlagged = languageCheck.schema_version === "1.0.0" && languageCheck.scope === "GENERATED_HYPOTHESIS_PROSE" &&
    languageCheck.response_language === "en" && typeof languageCheck.flagged_items === "number" && languageCheck.flagged_items > 0;
  const hypotheses = objectList(objectValue(result?.portfolio).hypotheses);
  const actions = objectList(objectValue(result?.action_plan).alternatives);
  const view = interruptionView({ state, result, error, failure, terminalReason });
  const { content, interrupted } = view;
  const shown = Boolean(result && content && !unavailable);
  const cite = (spanId: string, citations: Record<string, number>) => onDetail({ kind: "evidence", result: result ?? {}, historySelection, focusSpanId: spanId, citations });
  return <ResponseLanguageContext.Provider value={resultLanguage(result)}><article className="assistant-message">
    <header>THOTH{shown && result && <ResultStatusStrip result={result} coverage={coverageMatrix}/>}</header>
    <ResultInterruptionNotice view={view} result={result} unavailable={unavailable} hasSavedResult={hasSavedResult} historySelection={historySelection}
      onDetail={onDetail} onRetry={onRetry} onResume={onResume} resumeCompleted={resumeCompleted} resuming={resuming}/>
    {content && !unavailable && (currentness && currentness.state !== "CURRENT" ? <p className="result-notice">{currentnessLabel(currentness)}{" "}{t("labels.pastAnswer")}{currentnessReasonNote(currentness) && ` ${currentnessReasonNote(currentness)}.`}</p>
      : state === "STALE" && <p className="result-notice">{t("result.stale")}</p>)}
    {unavailable && <p className="result-notice">{unavailable}</p>}
    {shown && result ? <>
      <GroundingRowsNotice result={result}/>
      {languageFlagged && <p className="result-notice" role="status">{t("language.proseFlag")}</p>}
      <TraceOriginLine result={result} onOpenTrace={onOpenTrace}/>
      {typeof result.answer === "string" && <AnswerBody answer={result.answer} language={resultLanguage(result)} onCite={cite}/>}
      <nav className="answer-actions" aria-label={t("result.details")}>
        <Button small minimal icon="search" onClick={()=>onDetail({kind:"evidence",result,historySelection})}>{t("result.sources")}</Button>
        {Object.hasOwn(objectValue(result.portfolio), "hypotheses")&&<Button small minimal icon="lightbulb" onClick={()=>onDetail({kind:"hypotheses",result,historySelection})}>{t("result.compareHypotheses")}</Button>}
        {actions.length>0&&<Button small minimal icon="play" onClick={()=>onDetail({kind:"actions",result,historySelection})}>{t("result.compareActions")}</Button>}
        {historySelection&&<Button small minimal icon="history" onClick={()=>onDetail({kind:"history",result,historySelection})}>{t("result.changes")}</Button>}
        <Disclosure className="answer-more" label={t("result.more")}><div className="answer-more-actions">{([
          ["reference",t("result.references")],["map",t("result.map")],["outcome",t("result.outcome")],["memory",t("result.memory")],
        ] as const).map(([kind,label])=><Button small minimal key={kind} onClick={()=>onDetail({kind,result,historySelection})}>{label}</Button>)}</div></Disclosure>
      </nav>
      {executionHold && projectId && <ExecutionHoldNotice hold={executionHold} projectId={projectId} causeRef={holdCauseRef ?? "hold:" + (executionHold.plan_execution_id ?? "none")} onCleared={onRetry}/>}
      <ResultHoldDetails result={result} historySelection={historySelection} coverageMatrix={coverageMatrix} progressSummary={progressSummary} nextUserAction={nextUserAction} decisionDelta={decisionDelta} onDetail={onDetail}/>
      {Object.hasOwn(objectValue(result.portfolio), "hypotheses") && <section className="conversation-findings"><h3>{t("result.alternatives")}</h3>
        {causeUnconfirmed(result) && <CauseUnconfirmed/>}
        {hypotheses.length ? hypotheses.map((h,index)=><div key={textValue(h.hypothesis_id)||index}><p>{textValue(h.statement)}</p>{textValue(h.uncertainty)&&<small>{textValue(h.uncertainty)}</small>}{projectId&&<HypothesisLinkNotice projectId={projectId} link={hypothesisLinks?.find(item=>item.hypothesis_id===textValue(h.hypothesis_id))} all={hypothesisLinks} distribution={linkDistribution}/>}</div>) : <p>{t("result.noHypotheses")}</p>}
      </section>}
      {actions.length > 0 && <section className="conversation-findings"><h3>{t("result.nextActions")}</h3>{actions.map((action,index)=><div key={textValue(action.action_id)||index}><p>{textValue(action.specification)}</p><small>{textValue(action.expected_information_value)}</small>{action.execution_authority === "HUMAN_REQUIRED_R3" && <p className="result-notice">{t("result.authorityRequired")}</p>}</div>)}</section>}
      {usageLine && <p className="usage-line muted">{usageLine}</p>}
    </> : !unavailable && !interrupted && <p className="muted">{state === "RUNNING" ? t("result.checking") : t("result.noLinkedAnswer")}</p>}
    {result && !unavailable && <AbsenceChecks result={result}/>}
    {result && !unavailable && <CheckedObservations result={result} onCite={spanId=>onDetail({kind:"evidence",result,historySelection,focusSpanId:spanId})}/>}
  </article></ResponseLanguageContext.Provider>;
}
