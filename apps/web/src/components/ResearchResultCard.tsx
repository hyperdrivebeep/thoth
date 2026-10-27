import { message } from "../i18n/formatMessages";
import { ResponseLanguageContext, resultLanguage } from "../i18n/ResponseLanguageContext";
import { t, useTranslation } from "../i18n/locale";
import { Button } from "@blueprintjs/core";

export type DetailKind = "evidence" | "hypotheses" | "reference" | "actions" | "map" | "outcome" | "memory" | "history" | "diagnostics";
export type ResearchDetail = { kind: DetailKind; result: Record<string, unknown>; historySelection?: HistorySelection;
  /** Source to scroll to and highlight, and the numbers the answer gave each source. */
  focusSpanId?: string; citations?: Record<string, number> };
import { hasResearchContent, objectValue, objectList, stringValues, textValue } from "../api/presentation";
import type { ExecutionError, ResearchFailure } from "../api/research";
import { reasonText } from "./connectionCopy";
import type { Currentness, HistorySelection } from "../api/historyModels";
import type { CoverageMatrix, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { currentnessLabel, currentnessReasonNote } from "./history/historyPresentation";
import { Disclosure } from "./Disclosure";
import { ResearchFollowupSummary } from "./ResearchFollowupSummary";
import { AnswerBody } from "./AnswerBody";
import { AnswerHold } from "./AnswerHold";
import { ConfirmedFacts } from "./ConfirmedFacts";
import { CheckedObservations } from "./CheckedObservations";
import { AbsenceChecks } from "./AbsenceChecks";
import { TraceOriginLine } from "./TraceOriginLine";
import { HypothesisLinkNotice } from "./HypothesisLinkNotice";
import type { HypothesisLink } from "./hypothesisLinkText";
import type { ReasonDistribution } from "../api/hypothesisLink";
import { counterSearchLine } from "./hypothesisView";
import { causeUnconfirmed } from "./hypothesisReview";
import { CauseUnconfirmed } from "./HypothesisFields";
import { nextActionLabel } from "./statusLabels";
import { ReadableList } from "./ReadableList";
import { ExecutionHoldNotice } from "./ExecutionHoldNotice";
import { GroundingHoldNotice, GroundingRowsNotice } from "./GroundingNotice";
import type { ExecutionHold } from "../api/research";

function selectedEvidenceCount(result: Record<string, unknown>): number | undefined {
  const direct = result.selected_evidence_count ?? objectValue(result.retrieval).selected_count;
  if (typeof direct === "number" && direct > 0) return direct;
  return Array.isArray(result.selected_evidence_refs) && result.selected_evidence_refs.length ? result.selected_evidence_refs.length : undefined;
}

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
  const gaps = stringValues(objectValue(result?.assessment).missing_items);
  const failed=state==='FAILED';
  const cancelled=state==='CANCELLED';
  const cause=failure ?? error?.data?.failure;
  const code=cause?.primary.reason_code ?? error?.data?.reason_code ?? terminalReason ?? textValue(result?.terminal_reason);
  const groundingHold=code==='GROUNDING_PUBLICATION_BINDING_MISMATCH';
  const interrupted=cancelled || failed || groundingHold || Boolean(terminalReason && terminalReason!=='BOUNDED_RESEARCH_COMPLETE');
  const publication=cause?.primary.origin==='RESULT_PUBLICATION'||cause?.secondary?.origin==='HOLD_PUBLICATION';
  const content=hasResearchContent(result);
  // A model call that stopped the research early (connection error or timeout) and left no answer.
  const cutOffKind=/OAUTH_STALLED_STREAM/.test(code) ? t("result.slow") : /OAUTH_RUNAWAY_OUTPUT/.test(code) ? t("result.runaway") : /OAUTH_TRANSPORT_FAILURE/.test(code) ? t("result.connectionError") : /OAUTH_DISPATCH_DEADLINE/.test(code) ? t("result.deadline")
    : /OAUTH_[A-Z_]*TIMEOUT|OAUTH_TRANSPORT_DEADLINE/.test(code) ? t("result.responseTimeout") : null;
  const cutOff=cutOffKind!==null && !(typeof result?.answer==='string' && result.answer.trim()!=='');
  return <ResponseLanguageContext.Provider value={resultLanguage(result)}><article className="assistant-message"><header>THOTH</header>
    {groundingHold&&!unavailable&&<GroundingHoldNotice hasSavedResult={hasSavedResult}/>}
    {cancelled&&!unavailable&&!groundingHold&&<div className="result-notice" role="alert"><p>{t("result.cancelled")}</p>
      <p>{code ? message("terminationReason", code) : t("result.cancelUnknown")}</p>
      {content ? <strong>{t("result.beforeCancel")}</strong> : <p>{t("result.noSavedAnswer")}</p>}
    </div>}
    {interrupted&&!cancelled&&!unavailable&&!groundingHold&&<div className="result-notice" role="alert"><p>{t("result.incomplete")}</p>
      {/TIME_BUDGET|DEADLINE/.test(code)&&<p>{t("result.timeLimit")}</p>}
      {code==='TOTAL_RESEARCH_BUDGET_EXHAUSTED'&&<p>{t("result.researchLimit")}</p>}
      {/OAUTH_REQUEST_REJECTED_429/.test(code)&&<p>{t("result.quota")}</p>}
      {code==='CLAUDE_CODE_LOGIN_REQUIRED'&&<p>{reasonText(code)}</p>}
      {/AUTH_REQUIRED_403/.test(code)&&<p>{t("result.authentication")}</p>}
      {/MODEL_NOT_SUPPORTED/.test(code)&&<p>{t("result.unsupportedModel")}</p>}
      {/OAUTH_REQUEST_REJECTED_400/.test(code)&&<p>{t("result.requestRejected")}</p>}
      {publication&&<p>{t("result.storageFailed")}</p>}
      {failed&&!publication&&!/TIME_BUDGET|DEADLINE|AUTH_REQUIRED|OAUTH_REQUEST_REJECTED|MODEL_NOT_SUPPORTED|CLAUDE_CODE_LOGIN_REQUIRED/.test(code)&&<p>{t("result.internalFailed")}</p>}
      {(!cause || cause.remote_observation==='UNKNOWN')&&!/AUTH_REQUIRED|OAUTH_REQUEST_REJECTED|MODEL_NOT_SUPPORTED|CLAUDE_CODE_LOGIN_REQUIRED/.test(code)&&<p>{t("result.remoteUnknown")}</p>}
      {cutOff&&<p><strong>{message("interruptedAnswer", cutOffKind)}</strong></p>}
      {cutOff&&result&&<ConfirmedFacts result={result} heading={t("result.beforeInterruption")} onlyWhenPresent onCite={(spanId,citations)=>onDetail({kind:"evidence",result,historySelection,focusSpanId:spanId,citations})}/>}
      {cutOff&&onRetry&&<div><Button small icon="refresh" onClick={onRetry}>{t("result.retryQuestion")}</Button><small className="muted">{t("result.retryNotice")}</small></div>}
      {cutOff&&onResume&&(resumeCompleted??0)>0&&<div><Button small intent="primary" icon="play" loading={resuming} onClick={onResume}>{t("result.resume")}</Button>
        <small className="muted">{message("resumeStages", resumeCompleted)}</small></div>}
      {content&&<strong>{t("result.beforeFailure")}</strong>}
    </div>}
    {content && !unavailable && (currentness && currentness.state !== "CURRENT" ? <p className="result-notice">{currentnessLabel(currentness)}{" "}{t("labels.pastAnswer")}{currentnessReasonNote(currentness) && ` ${currentnessReasonNote(currentness)}.`}</p>
      : state === "STALE" && <p className="result-notice">{t("result.stale")}</p>)}
    {unavailable && <p className="result-notice">{unavailable}</p>}
    {result && content && !unavailable ? <>
      <GroundingRowsNotice result={result}/>
      {languageFlagged && <p className="result-notice" role="status">{t("language.proseFlag")}</p>}
      <TraceOriginLine result={result} onOpenTrace={onOpenTrace}/>
      {typeof result.answer === "string" && <AnswerBody answer={result.answer} language={resultLanguage(result)}
        onCite={(spanId, citations) => onDetail({kind:"evidence",result,historySelection,focusSpanId:spanId,citations})}/>}
      {typeof result.answer_status === "string" && <AnswerHold status={result.answer_status} coverage={coverageMatrix} counter={counterSearchLine(result)}
        facts={<ConfirmedFacts result={result} onCite={(spanId, citations) => onDetail({kind:"evidence",result,historySelection,focusSpanId:spanId,citations})}/>}
        next={(() => { const step = nextUserAction ?? progressSummary?.next_user_action; return step && step.action_type !== "NONE" ? nextActionLabel(step) : null; })()}/>}
      {executionHold && projectId && <ExecutionHoldNotice hold={executionHold} projectId={projectId} causeRef={holdCauseRef ?? "hold:" + (executionHold.plan_execution_id ?? "none")} onCleared={onRetry}/>}
      <ResearchFollowupSummary answerEvidenceCount={selectedEvidenceCount(result)} progress={progressSummary} coverage={coverageMatrix} action={nextUserAction} delta={decisionDelta}
        onOpenEvidence={historySelection ? () => onDetail({kind:"evidence",result,historySelection}) : undefined}/>
      {gaps.length > 0 && <div className="result-notice"><strong>{t("result.openChecks")}</strong><ReadableList items={gaps}/></div>}
      {Object.hasOwn(objectValue(result.portfolio), "hypotheses") && <section className="conversation-findings"><h3>{t("result.alternatives")}</h3>
        {causeUnconfirmed(result) && <CauseUnconfirmed/>}
        {hypotheses.length ? hypotheses.map((h,index)=><div key={textValue(h.hypothesis_id)||index}><p>{textValue(h.statement)}</p>{textValue(h.uncertainty)&&<small>{textValue(h.uncertainty)}</small>}{projectId&&<HypothesisLinkNotice projectId={projectId} link={hypothesisLinks?.find(item=>item.hypothesis_id===textValue(h.hypothesis_id))} all={hypothesisLinks} distribution={linkDistribution}/>}</div>) : <p>{t("result.noHypotheses")}</p>}
      </section>}
      {actions.length > 0 && <section className="conversation-findings"><h3>{t("result.nextActions")}</h3>{actions.map((action,index)=><div key={textValue(action.action_id)||index}><p>{textValue(action.specification)}</p><small>{textValue(action.expected_information_value)}</small>{action.execution_authority === "HUMAN_REQUIRED_R3" && <p className="result-notice">{t("result.authorityRequired")}</p>}</div>)}</section>}
      <nav className="answer-actions" aria-label={t("result.details")}>
        <Button small minimal icon="search" onClick={()=>onDetail({kind:"evidence",result,historySelection})}>{t("result.sources")}</Button>
        {Object.hasOwn(objectValue(result.portfolio), "hypotheses")&&<Button small minimal icon="lightbulb" onClick={()=>onDetail({kind:"hypotheses",result,historySelection})}>{t("result.compareHypotheses")}</Button>}
        {actions.length>0&&<Button small minimal icon="play" onClick={()=>onDetail({kind:"actions",result,historySelection})}>{t("result.compareActions")}</Button>}
        {historySelection&&<Button small minimal icon="history" onClick={()=>onDetail({kind:"history",result,historySelection})}>{t("result.changes")}</Button>}
        <Disclosure className="answer-more" label={t("result.more")}><div className="answer-more-actions">{([
          ["reference",t("result.references")],["map",t("result.map")],["outcome",t("result.outcome")],["memory",t("result.memory")],
        ] as const).map(([kind,label])=><Button small minimal key={kind} onClick={()=>onDetail({kind,result,historySelection})}>{label}</Button>)}</div></Disclosure>
      </nav>
      {usageLine && <p className="usage-line muted">{usageLine}</p>}
    </> : !unavailable && !interrupted && <p className="muted">{state === "RUNNING" ? t("result.checking") : t("result.noLinkedAnswer")}</p>}
    {result && !unavailable && <AbsenceChecks result={result}/>}
    {result && !unavailable && <CheckedObservations result={result} onCite={spanId=>onDetail({kind:"evidence",result,historySelection,focusSpanId:spanId})}/>}
  </article></ResponseLanguageContext.Provider>;
}
