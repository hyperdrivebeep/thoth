import { Button, Callout, TextArea } from "@blueprintjs/core";
import { useTranslation, t } from "../i18n/locale";
import { message } from "../i18n/formatMessages";
import { languageLabel } from "../i18n/languageChoices";
import { ModelSettings } from "./ModelSettings";
import { ResearchProgress, UsageSummary } from "./ResearchProgress";
import { useCitationGate } from "./SourceCitationGate";
import type { useResearchSession } from "./useResearchSession";

export function ResearchComposer({session,projectId,threadId,epoch,onAttach,onOpenSettings,hosted=false,executionReady=true,readSuspended=false,consentMissing=false,modelConnected}: {
  session:ReturnType<typeof useResearchSession>;projectId:string;threadId:string;epoch:number;onAttach:()=>void;onOpenSettings?:(target?:"consent")=>void;
  hosted?:boolean;executionReady?:boolean;readSuspended?:boolean;consentMissing?:boolean;modelConnected?:boolean;
}) {
  useTranslation();
  const citation=useCitationGate(projectId, session.isCurrentScope);
  const acceptedRequest = session.research.data?.value.request;
  const acceptedLanguage = acceptedRequest?.response_language ?? null;
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
    {!readSuspended && !hosted && !executionReady && <Callout compact intent="warning" role="status">{t("composer.savedResearch")} {blockedReason(consentMissing,modelConnected)}
      {onOpenSettings && (consentMissing && modelConnected!==false
        ? <Button small minimal onClick={()=>onOpenSettings("consent")}>{t("composer.chooseConsent")}</Button>
        : <Button small minimal onClick={()=>onOpenSettings()}>{t("composer.checkModel")}</Button>)}</Callout>}
    {!hosted && session.draftRestoreIssue && <Callout compact intent="warning" role="alert">{session.draftRestoreIssue === "CORRUPT"
      ? t("composer.corruptDraft")
      : t("composer.unavailableDraft")}
      {" "}{t("composer.draftScope")}</Callout>}
    {!hosted && session.draftSaveState !== "SAVED" && <Callout compact intent="warning" role="status">{session.draftSaveState === "TOO_LARGE"
      ? t("composer.draftTooLarge")
      : t("composer.draftUnsaved")}</Callout>}
    {!readSuspended&&citation.dialog}{!readSuspended&&citation.message&&<Callout compact>{citation.message}</Callout>}<form className="prompt-composer" onSubmit={event=>{event.preventDefault();void submit();}}>
      <label className="composer-label" htmlFor="live-problem">{t("composer.problem")}</label>
      <TextArea id="live-problem" fill rows={2} value={session.problem} onChange={event=>session.setProblem(event.target.value)}
        placeholder={threadId?t("composer.followupPlaceholder"):t("composer.startPlaceholder")}/>
      {session.responseLanguage && !session.languageSupported && <small className="language-help">{t("language.unsupported")}</small>}
      <div className="composer-tools"><Button minimal icon="paperclip" aria-label={t("composer.attach")} title={t("composer.attach")} disabled={readSuspended} onClick={onAttach}>{t("composer.attachShort")}</Button>
      {session.responseLanguage && <span className="language-fixed" title={t("language.fixed")}>{message("replyFixed", languageLabel(session.responseLanguage))}
        {onOpenSettings && <> · <Button small minimal className="language-fixed-link" onClick={()=>onOpenSettings()}>{t("language.changeInSettings")}</Button></>}</span>}
      {acceptedRequest && acceptedLanguage !== session.responseLanguage && <small className="language-current" title={t("language.fixed")}>{t("language.acceptedShort")}: {acceptedLanguage === "en" ? "English" : acceptedLanguage === "ko" ? "한국어" : t("language.automatic")}</small>}
        {hosted || readSuspended ? null : <ModelSettings compact key={`${projectId}:${threadId||"new"}`} projectId={projectId} threadId={threadId||undefined}
          selection={session.modelSelection} onSelect={session.chooseModel} onSaved={()=>session.clearModel(session.renderedRevision,epoch)}/>}
        {onOpenSettings && <Button minimal icon="settings" aria-label={t("workspace.settings")} title={t("workspace.settings")} onClick={()=>onOpenSettings()}/>}
        <Button icon="arrow-up" intent="primary" type="submit" aria-label={threadId?t("composer.followup"):t("composer.start")} title={threadId?t("composer.followup"):t("composer.start")} disabled={readSuspended || !executionReady || session.pendingBlocksNewInput || !session.problem.trim()} loading={session.submit.isPending}>
          {session.pendingRead.kind === "PENDING" ? t("composer.checkOriginal") : null}</Button>
      </div>
      {session.submit.error&&<Callout compact intent="danger" role="alert">{session.submit.error.message}</Callout>}
    </form>
    {!readSuspended&&<UsageSummary status={session.research.data?.value}/>}
  </div>;
}

/** What actually blocks a new research: an empty internet consent, the model, both, or something the screen cannot name. */
function blockedReason(consentMissing: boolean, modelConnected: boolean | undefined): string {
  if (consentMissing) return modelConnected === false ? t("composer.needBoth") : t("composer.needConsent");
  return modelConnected === false ? t("composer.needModel") : t("composer.needSetup");
}
