import { Button, Callout, TextArea } from "@blueprintjs/core";
import { useTranslation, t } from "../i18n/locale";
import { sentenceGap } from "../i18n/sentences";
import { blockedReason } from "./readinessText";
import { message } from "../i18n/formatMessages";
import { isResponseLanguage, responseLanguageLabel } from "../api/responseLanguages";
import { ModelSettings } from "./ModelSettings";
import { ResearchProgress, UsageSummary } from "./ResearchProgress";
import { useCitationGate } from "./SourceCitationGate";
import type { useResearchSession } from "./useResearchSession";

/** The notice that says why a new run cannot start; the send button points at it. */
const BLOCKED_REASON_ID = "composer-blocked-reason";

export function ResearchComposer({session,projectId,threadId,epoch,onAttach,onOpenSettings,hosted=false,executionReady=true,readSuspended=false,consentMissing=false,modelConnected,setupNoticeShown=false}: {
  session:ReturnType<typeof useResearchSession>;projectId:string;threadId:string;epoch:number;onAttach:()=>void;onOpenSettings?:(target?:"consent")=>void;
  hosted?:boolean;executionReady?:boolean;readSuspended?:boolean;consentMissing?:boolean;modelConnected?:boolean;
  /** The readiness card above already says what blocks a new run; do not repeat it here. */
  setupNoticeShown?:boolean;
}) {
  useTranslation();
  const citation=useCitationGate(projectId, session.isCurrentScope);
  const acceptedRequest = session.research.data?.value.request;
  const acceptedLanguage = acceptedRequest?.response_language ?? null;
  const setupNoticeHidden = readSuspended || hosted || executionReady || setupNoticeShown;
  const submit = async () => {
    if (!session.isCurrentScope() || !executionReady || readSuspended || session.pendingBlocksNewInput) return;
    const refs=[...session.problem.matchAll(/artifact:[A-Za-z0-9._:-]+/g)].map(item=>item[0]);
    for (const artifactId of refs) {
      const result=await citation.ask(artifactId);
      if(!session.isCurrentScope() || !result || result.status!=="ELIGIBLE") return;
    }
    if (session.isCurrentScope()) session.send();
  };
  return <div className="composer-dock">
    {!readSuspended && session.research.data?.value && <ResearchProgress status={session.research.data.value}/>}
    {session.pendingRead.kind === "PENDING" && <Callout compact intent="warning" role="status">{session.submit.isPending
      ? t("composer.checking")
      : t("composer.uncertain")}
      {!session.submit.isPending && <Button small minimal disabled={!executionReady || readSuspended || !session.languageSupported && session.pendingRead.kind === "PENDING" && session.pendingRead.value.version === 2} onClick={session.retryPending}>{t("composer.retryOriginal")}</Button>}</Callout>}
    {session.pendingRead.kind === "INVALID" && <Callout compact intent="danger" role="alert">{t("composer.invalidPending")}</Callout>}
    {session.pendingStorageError && <Callout compact intent="warning" role="alert">{t(session.pendingStorageError)}</Callout>}
    {readSuspended && <Callout compact intent="warning" role="status">{t("composer.accessChecking")}</Callout>}
    <SetupNotice hidden={setupNoticeHidden} consentMissing={consentMissing} modelConnected={modelConnected} onOpenSettings={onOpenSettings}/>
    {!hosted && session.draftRestoreIssue && <Callout compact intent="warning" role="alert">{session.draftRestoreIssue === "CORRUPT"
      ? t("composer.corruptDraft")
      : t("composer.unavailableDraft")}
      {sentenceGap()}{t("composer.draftScope")}</Callout>}
    {!hosted && session.draftSaveState !== "SAVED" && <Callout compact intent="warning" role="status">{session.draftSaveState === "TOO_LARGE"
      ? t("composer.draftTooLarge")
      : t("composer.draftUnsaved")}</Callout>}
    {!readSuspended&&citation.dialog}{!readSuspended&&citation.message&&<Callout compact>{citation.message}</Callout>}<form className="prompt-composer" onSubmit={event=>{event.preventDefault();void submit();}}>
      <label className="composer-label" htmlFor="live-problem">{t("composer.problem")}</label>
      <TextArea
        id="live-problem"
        fill
        rows={1}
        autoResize
        value={session.problem}
        onChange={event => session.setProblem(event.target.value)}
        placeholder={threadId ? t("composer.followupPlaceholder") : t("composer.startPlaceholder")}
      />
      {session.responseLanguage && !session.languageSupported && <small className="language-help">{t("language.unsupported")}</small>}
      <div className="composer-tools"><Button minimal icon="paperclip" aria-label={t("composer.attach")} title={t("composer.attach")} disabled={readSuspended} onClick={onAttach}>{t("composer.attachShort")}</Button>
      {session.responseLanguage && <span className="language-fixed" title={t("language.fixed")}>{message("replyFixed", responseLanguageLabel(session.responseLanguage))}
        {onOpenSettings && <> · <Button small minimal className="language-fixed-link" onClick={()=>onOpenSettings()}>{t("language.changeInSettings")}</Button></>}</span>}
      {acceptedRequest && acceptedLanguage !== session.responseLanguage && <small className="language-current" title={t("language.fixed")}>{t("language.acceptedShort")}: {isResponseLanguage(acceptedLanguage) ? responseLanguageLabel(acceptedLanguage) : t("language.automatic")}</small>}
        <ComposerModelChoice
          hidden={hosted || readSuspended || modelConnected === false}
          session={session}
          projectId={projectId}
          threadId={threadId}
          epoch={epoch}
        />
        {onOpenSettings && <Button minimal icon="settings" aria-label={t("workspace.settings")} title={t("workspace.settings")} onClick={()=>onOpenSettings()}/>}
        <SendButton
          followup={Boolean(threadId)}
          checkingOriginal={session.pendingRead.kind === "PENDING"}
          disabled={readSuspended || !executionReady || session.pendingBlocksNewInput || !session.problem.trim()}
          loading={session.submit.isPending}
          blockedReasonText={executionReady ? undefined : blockedReason(consentMissing, modelConnected)}
          describedBy={setupNoticeHidden ? undefined : BLOCKED_REASON_ID}
        />
      </div>
      {session.submit.error&&<Callout compact intent="danger" role="alert">{session.submit.error.message}</Callout>}
    </form>
    {!readSuspended&&<UsageSummary status={session.research.data?.value}/>}
  </div>;
}

/**
 * The model and effort choice for the next request. It is left out while no model is connected:
 * there is nothing to choose from, and the notice above already says what to do.
 */
function ComposerModelChoice({ hidden, session, projectId, threadId, epoch }: {
  hidden: boolean;
  session: ReturnType<typeof useResearchSession>;
  projectId: string;
  threadId: string;
  epoch: number;
}) {
  if (hidden) return null;
  return (
    <ModelSettings
      compact
      key={`${projectId}:${threadId || "new"}`}
      projectId={projectId}
      threadId={threadId || undefined}
      selection={session.modelSelection}
      onSelect={session.chooseModel}
      onSaved={() => session.clearModel(session.renderedRevision, epoch)}
    />
  );
}

/** The send button. While setup blocks a new run, its title says why and it points at the notice. */
function SendButton({ followup, checkingOriginal, disabled, loading, blockedReasonText, describedBy }: {
  followup: boolean;
  checkingOriginal: boolean;
  disabled: boolean;
  loading: boolean;
  blockedReasonText?: string;
  describedBy?: string;
}) {
  const label = followup ? t("composer.followup") : t("composer.start");
  return (
    <Button
      icon="arrow-up"
      intent="primary"
      type="submit"
      aria-label={label}
      aria-describedby={describedBy}
      title={blockedReasonText ?? label}
      disabled={disabled}
      loading={loading}
    >
      {checkingOriginal ? t("composer.checkOriginal") : null}
    </Button>
  );
}

/** Why a new run cannot start yet, with the one button that opens the setting in question. */
function SetupNotice({ hidden, consentMissing, modelConnected, onOpenSettings }: { hidden: boolean; consentMissing: boolean; modelConnected: boolean | undefined; onOpenSettings?: (target?: "consent") => void }) {
  if (hidden) return null;
  const consentFirst = consentMissing && modelConnected !== false;
  return <Callout compact intent="warning" role="status" id={BLOCKED_REASON_ID}>{t("composer.savedResearch")}{sentenceGap()}{blockedReason(consentMissing, modelConnected)}
    {onOpenSettings && (consentFirst
      ? <Button small minimal onClick={() => onOpenSettings("consent")}>{t("composer.chooseConsent")}</Button>
      : <Button small minimal onClick={() => onOpenSettings()}>{t("composer.checkModel")}</Button>)}</Callout>;
}
