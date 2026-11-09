import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button } from "@blueprintjs/core";
import type { HistorySelection } from "../api/historyModels";
import { reasonText } from "./connectionCopy";
import { ConfirmedFacts } from "./ConfirmedFacts";
import { GroundingHoldNotice } from "./GroundingNotice";
import type { ResearchDetail } from "./ResearchResultCard";
import type { InterruptionView } from "./resultInterruption";

const AUTH_LIKE = /AUTH_REQUIRED|OAUTH_REQUEST_REJECTED|MODEL_NOT_SUPPORTED|CLAUDE_CODE_LOGIN_REQUIRED/;
const TIME_LIKE = /TIME_BUDGET|DEADLINE/;

/** The sentences that explain an interrupted run, in display order; each is shown only when its code matches. */
function interruptionReasons({ code, cause, failed, publication, savedBeforeCut: stalled }: InterruptionView): string[] {
  const incompleteResponse = code === "OAUTH_INCOMPLETE_RESPONSE";
  const lines: Array<[boolean, string]> = [
    [TIME_LIKE.test(code) && !stalled, t("result.timeLimit")],
    [code === "TOTAL_RESEARCH_BUDGET_EXHAUSTED", t("result.researchLimit")],
    [/OAUTH_REQUEST_REJECTED_429/.test(code), t("result.quota")],
    [code === "CLAUDE_CODE_LOGIN_REQUIRED", reasonText(code) ?? code],
    [/AUTH_REQUIRED_403/.test(code), t("result.authentication")],
    [/MODEL_NOT_SUPPORTED/.test(code), t("result.unsupportedModel")],
    [/OAUTH_REQUEST_REJECTED_400/.test(code), t("result.requestRejected")],
    [publication, t("result.storageFailed")],
    [failed && !publication && !TIME_LIKE.test(code) && !AUTH_LIKE.test(code), t("result.internalFailed")],
    [stalled, t("result.stalled")],
    [incompleteResponse, t("result.incompleteResponse")],
    [(!cause || cause.remote_observation === "UNKNOWN") && !AUTH_LIKE.test(code) && !stalled && !incompleteResponse, t("result.remoteUnknown")],
  ];
  return lines.filter(([shown]) => shown).map(([, text]) => text);
}

type CutOffProps = {
  view: InterruptionView; result: Record<string, unknown> | null; historySelection?: HistorySelection;
  onDetail: (detail: ResearchDetail) => void; onRetry?: () => void; onResume?: () => void; resumeCompleted?: number; resuming?: boolean;
};

/** What a cut-off model call left behind and the two ways to go on: fill the question in again, or resume from the finished stages. */
function CutOffActions({ view, result, historySelection, onDetail, onRetry, onResume, resumeCompleted = 0, resuming }: CutOffProps) {
  if (!view.cutOff || !view.cutOffKind) return null;
  return <>
    <p><strong>{message("interruptedAnswer", t(`result.${view.cutOffKind}`))}</strong></p>
    {result && <ConfirmedFacts result={result} heading={t("result.beforeInterruption")} onlyWhenPresent
      onCite={(spanId, citations) => onDetail({ kind: "evidence", result, historySelection, focusSpanId: spanId, citations })}/>}
    {onRetry && <div><Button small icon="refresh" onClick={onRetry}>{t("result.retryQuestion")}</Button><small className="muted">{t("result.retryNotice")}</small></div>}
    {onResume && resumeCompleted > 0 && <div><Button small intent="primary" icon="play" loading={resuming} onClick={onResume}>{t("result.resume")}</Button>
      <small className="muted">{message("resumeStages", resumeCompleted)}</small></div>}
  </>;
}

/** The project's automatic retry is off: say so next to a cut-off model call, with the way to the setting. */
function AutoRetryOffNotice({ onOpenSettings }: { onOpenSettings?: () => void }) {
  return <div role="status"><p>{t("result.autoRetryOff")}</p>
    {onOpenSettings && <Button small minimal icon="settings" onClick={onOpenSettings}>{t("result.autoRetryOpenSettings")}</Button>}</div>;
}

/** Why a research run did not finish its bounded scope, and what the user can do with what was saved. */
export function ResultInterruptionNotice({ unavailable, hasSavedResult, autoRetryOff = false, onOpenSettings, ...props }: CutOffProps & { unavailable?: string; hasSavedResult: boolean; autoRetryOff?: boolean; onOpenSettings?: () => void }) {
  const { view } = props;
  if (unavailable) return null;
  if (view.groundingHold) return <GroundingHoldNotice hasSavedResult={hasSavedResult}/>;
  if (view.cancelled) return <div className="result-notice" role="alert"><p>{t("result.cancelled")}</p>
    <p>{view.code ? message("terminationReason", view.code) : t("result.cancelUnknown")}</p>
    {view.content ? <strong>{t("result.beforeCancel")}</strong> : <p>{t("result.noSavedAnswer")}</p>}
  </div>;
  if (!view.interrupted) return null;
  return <div className="result-notice" role="alert"><p>{t(view.savedBeforeCut ? "result.savedBeforeCut" : "result.incomplete")}</p>
    {interruptionReasons(view).map(line => <p key={line}>{line}</p>)}
    <CutOffActions {...props}/>
    {autoRetryOff && (view.cutOffKind !== null || view.savedBeforeCut) && <AutoRetryOffNotice onOpenSettings={onOpenSettings}/>}
    {view.content && <strong>{t("result.beforeFailure")}</strong>}
  </div>;
}
