import { localeTag } from "../i18n/locale";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Collapse } from "@blueprintjs/core";
import { useState } from "react";
import type { ResearchStatus } from "../api/research";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import type { ProjectSummary } from "../types";
import { eyebrowLabel } from "./statusLabels";
import { AutoRetrySwitch } from "./AutoRetrySwitch";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { HypothesisContractSwitch } from "./HypothesisContractSwitch";
import { LanguageSettingsCard } from "./LanguageSettingsCard";
import { LocalRetrievalSettingsCard } from "./LocalRetrievalSettingsCard";
import { ModelCredentialPanel } from "./ModelCredentialPanel";
import { ModelSettings, type ModelSelection } from "./ModelSettings";
import { ProjectWebSetup } from "./ProjectWebSetup";
import { RecordInspector } from "./RecordInspector";
import { WorkspaceInternetConsent } from "./WorkspaceInternetConsent";
import type { useCitationGate } from "./SourceCitationGate";
import type { useProjectSources } from "./useProjectSources";
import { connectedArtifacts } from "../api/sourceBindings";
import { ConnectedSourceCard } from "./ConnectedSourceCard";
import { DetachedSources } from "./SourceDisconnect";
import type { useResearchSession } from "./useResearchSession";
import type { Page } from "./workspaceSelection";

type SourcesRead = ReturnType<typeof useProjectSources>["data"];

/** The project's sources: what is connected first, then adding a file, then the public web sources. */
export function ResourcesPage({ project, hosted, citation, sourceData, sourcesError, sourcesDenied, onPage, onOpenConsent }: {
  project: ProjectSummary; hosted: boolean; citation: ReturnType<typeof useCitationGate>; sourceData: SourcesRead; sourcesError: Error | null; sourcesDenied: boolean; onPage: (page: Page) => void;
  /** Opens the setting that allows internet use. Not offered in the hosted review environment, which cannot change it. */
  onOpenConsent?: () => void;
}) {
  const connected = connectedArtifacts(sourceData?.value);
  const failed = Boolean(sourcesError || sourcesDenied);
  return <>
    <header className="workspace-heading">
      <div><h1>{t("workspace.resources")}</h1><p>{t("workspace.resourcesNotice")}</p></div>
      <Button minimal icon="arrow-left" onClick={()=>onPage("research")}>{t("workspace.backConversation")}</Button>
    </header>
    <section className="connected-sources" aria-labelledby="connected-sources-title">
      <h2 id="connected-sources-title">{t("workspace.connectedSources")}</h2>
      {citation.dialog}
      {citation.message&&<Callout compact role="status">{citation.message}</Callout>}
      {sourceData && !failed && connected.length === 0 && <p className="empty-copy">{t("workspace.noConnectedSources")}</p>}
      {connected.map(artifact => (
        <ConnectedSourceCard key={artifact.artifact_id} projectId={project.project_id} artifact={artifact} sources={sourceData?.value} hosted={hosted}
          onCite={() => void citation.ask(artifact.artifact_id)} />
      ))}
      <DetachedSources sources={sourceData?.value}/>
      {failed&&<Callout intent="danger" role="alert">{sourcesError?.message ?? t("workspace.accessFailed")}</Callout>}
    </section>
    <FileConnectionPanel projectId={project.project_id} hosted={hosted}/>
    <ProjectWebSetup projectId={project.project_id} onOpenConsent={hosted ? undefined : onOpenConsent}/>
  </>;
}

/** Reported usage, the project record, and the records/developer tools. */
function UsageAndTools({ project, hosted, status, onPage, onOpenCatalog }: { project: ProjectSummary; hosted: boolean; status: ResearchStatus | undefined; onPage: (page: Page) => void; onOpenCatalog: () => void }) {
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [projectDetailsOpen, setProjectDetailsOpen] = useState(false);
  return <>
    <section className="detail-card"><h2>{t("workspace.usage")}</h2><p>{message("reportedTokens", status?.usage?.total_tokens?.toLocaleString(localeTag())??t("workspace.noUsage"), status?.usage?.state==="PARTIAL"?t("workspace.partialUsage"):"")}</p><p>{t("workspace.usagePending")}</p>{!hosted&&<><Button small minimal icon={projectDetailsOpen?"chevron-up":"settings"} onClick={()=>setProjectDetailsOpen(value=>!value)} aria-expanded={projectDetailsOpen}>{t("workspace.projectSettings")}</Button><Collapse isOpen={projectDetailsOpen}><RecordInspector value={project}/></Collapse></>}</section>
    <section className="detail-card"><h2>{hosted?t("workspace.recordsSources"):t("workspace.recordsTools")}</h2><Button minimal icon="folder-open" onClick={()=>onPage("resources")}>{t("workspace.manageSources")}</Button><Button minimal icon="history" onClick={()=>onPage("records")}>{t("workspace.history")}</Button>{!hosted&&<><Button minimal icon={developerOpen?"chevron-up":"chevron-down"} onClick={()=>setDeveloperOpen(value=>!value)} aria-expanded={developerOpen}>{t("workspace.devTools")}</Button><Collapse isOpen={developerOpen}><div className="local-developer-tools"><Button minimal icon="database" onClick={()=>onPage("raw-records")}>{t("workspace.detailedRecords")}</Button><Button minimal icon="code" onClick={onOpenCatalog}>{t("workspace.apiTools")}</Button><RecordInspector value={{request:status?.request,attempt:status?.attempt,budget:status?.budget,usage:status?.usage,cleanup:status?.cleanup}}/></div></Collapse></>}</section>
  </>;
}

type SettingsProps = {
  project: ProjectSummary; threadId: string; hosted: boolean; workspaceId: string | null; storageScope: BrowserScope; session: ReturnType<typeof useResearchSession>; capturedEpoch: number;
  status: ResearchStatus | undefined; consent?: string; focusConsent: boolean; onConsentFocused: () => void; onPage: (page: Page) => void; onOpenCatalog: () => void;
  /** False when no model account is connected on this computer; undefined while that is not known. */
  modelConnected?: boolean;
};

/** The settings screen for the page that is open: this computer's, or the project's. */
export function SettingsPages({ page, ...props }: SettingsProps & { page: Page }) {
  if (page === "computer" && !props.hosted) {
    return <ComputerSettingsPage credentialProjectId={props.project.project_id} workspaceId={props.workspaceId} storageScope={props.storageScope}
      consent={props.consent} focusConsent={props.focusConsent} onConsentFocused={props.onConsentFocused} />;
  }
  return props.hosted ? <HostedInfoPage {...props} /> : <ProjectSettingsPage {...props} />;
}

/** The project's default model with a choice of its own; the conversation's choice lives in the question box. */
function ProjectModelDefault({ projectId }: { projectId: string }) {
  const [choice, setChoice] = useState<ModelSelection | null>(null);
  return <ModelSettings legend={t("settings.projectModel")} projectId={projectId} selection={choice} onSelect={setChoice} onSaved={() => setChoice(null)} />;
}

/**
 * This project's settings: the model it uses by default (and the open conversation's own choice), how it
 * searches its own sources, its usage and its records. What holds for every project is on the other screen.
 */
function ProjectSettingsPage({ project, threadId, workspaceId, session, capturedEpoch, status, onPage, onOpenCatalog, modelConnected }: SettingsProps) {
  return <>
    <header className="workspace-heading">
      <div><h1>{t("settings.projectTitle")}</h1><p>{t("settings.projectNotice")}</p></div>
      <Button minimal icon="desktop" onClick={() => onPage("computer")}>{t("settings.openComputer")}</Button>
    </header>
    <section className="detail-card">
      <h2>{t("workspace.modelDefaults")}</h2>
      {modelConnected === false ? (
        <Callout compact intent="warning" className="settings-no-model">
          {t("settings.noModel")}
          <Button small minimal icon="desktop" onClick={() => onPage("computer")}>{t("settings.openComputer")}</Button>
        </Callout>
      ) : (
        <>
          <ProjectModelDefault projectId={project.project_id} />
          {threadId && <ModelSettings legend={t("settings.conversationModel")} projectId={project.project_id} threadId={threadId} selection={session.modelSelection}
            onSelect={session.chooseModel} onSaved={() => session.clearModel(session.renderedRevision, capturedEpoch)} />}
        </>
      )}
      <AutoRetrySwitch projectId={project.project_id}/>
      <HypothesisContractSwitch projectId={project.project_id}/>
    </section>
    <LocalRetrievalSettingsCard part="project" workspaceId={workspaceId ?? "UNBOUND"} projectId={project.project_id} />
    <UsageAndTools project={project} hosted={false} status={status} onPage={onPage} onOpenCatalog={onOpenCatalog}/>
  </>;
}

/** This computer's settings: model accounts, language, internet permission and the local search parts. They hold for every project. */
export function ComputerSettingsPage({ credentialProjectId, workspaceId, storageScope, consent, focusConsent = false, onConsentFocused }: {
  credentialProjectId: string; workspaceId: string | null; storageScope: BrowserScope; consent?: string; focusConsent?: boolean; onConsentFocused?: () => void;
}) {
  return <>
    <header className="workspace-heading">
      <div><h1>{t("settings.computerTitle")}</h1><p>{t("settings.computerNotice")}</p></div>
    </header>
    <section className="detail-card"><h2>{t("workspace.connectModel")}</h2><ModelCredentialPanel projectId={credentialProjectId} workspaceId={workspaceId ?? undefined}/></section>
    <LanguageSettingsCard scope={storageScope}/>
    {consent !== undefined && <WorkspaceInternetConsent consent={consent} focus={focusConsent} onFocused={onConsentFocused ?? (() => {})}/>}
    <LocalRetrievalSettingsCard part="computer" workspaceId={workspaceId ?? "UNBOUND"} />
  </>;
}

/** The hosted review environment: what the operator set, the language, usage and records. */
function HostedInfoPage({ project, storageScope, status, workspaceId, onPage, onOpenCatalog }: SettingsProps) {
  return <>
    <header className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel("REVIEW WORKSPACE")}</p><h1>{t("workspace.operations")}</h1><p>{t("workspace.reviewInfo")}</p></div></header>
    <section className="detail-card"><h2>{t("workspace.reviewModel")}</h2><p>{t("workspace.operatorModel")}</p></section>
    <LanguageSettingsCard scope={storageScope}/>
    <LocalRetrievalSettingsCard workspaceId={workspaceId ?? "UNBOUND"} projectId={project.project_id} hosted/>
    <UsageAndTools project={project} hosted status={status} onPage={onPage} onOpenCatalog={onOpenCatalog}/>
  </>;
}
