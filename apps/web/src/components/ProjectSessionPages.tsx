import { localeTag } from "../i18n/locale";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Collapse } from "@blueprintjs/core";
import { useState } from "react";
import type { ResearchStatus } from "../api/research";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import { cutoffLabel } from "../api/sourceTime";
import type { ProjectSummary } from "../types";
import { isHttpUrl, sourceCardHost, sourceCardTitle } from "../sourceDisplay";
import { eyebrowLabel } from "./statusLabels";
import { AutoRetrySwitch } from "./AutoRetrySwitch";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { HypothesisContractSwitch } from "./HypothesisContractSwitch";
import { LanguageSettingsCard } from "./LanguageSettingsCard";
import { LocalRetrievalSettingsCard } from "./LocalRetrievalSettingsCard";
import { ModelCredentialPanel } from "./ModelCredentialPanel";
import { ModelSettings } from "./ModelSettings";
import { ProjectWebSetup } from "./ProjectWebSetup";
import { RecordInspector } from "./RecordInspector";
import { SourceTimeAdvanced } from "./SourceTimeAdvanced";
import { WorkspaceInternetConsent } from "./WorkspaceInternetConsent";
import type { useCitationGate } from "./SourceCitationGate";
import type { useProjectSources } from "./useProjectSources";
import type { useResearchSession } from "./useResearchSession";
import type { Page } from "./workspaceSelection";

type SourcesRead = ReturnType<typeof useProjectSources>["data"];

/** The project's connected sources: web setup, file connection and the list of what is connected. */
export function ResourcesPage({ project, hosted, citation, sourceData, sourcesError, sourcesDenied, onPage }: {
  project: ProjectSummary; hosted: boolean; citation: ReturnType<typeof useCitationGate>; sourceData: SourcesRead; sourcesError: Error | null; sourcesDenied: boolean; onPage: (page: Page) => void;
}) {
  const [artifactDetailsId, setArtifactDetailsId] = useState("");
  return <><header className="workspace-heading"><div><h1>{t("workspace.resources")}</h1><p>{t("workspace.resourcesNotice")}</p></div><Button minimal icon="arrow-left" onClick={()=>onPage("research")}>{t("workspace.backConversation")}</Button></header><ProjectWebSetup projectId={project.project_id}/><FileConnectionPanel projectId={project.project_id} hosted={hosted}/><h2>{t("workspace.connectedSources")}</h2>
    {citation.dialog}{citation.message&&<Callout compact>{citation.message}</Callout>}{(sourceData?.value.artifacts??[]).map(artifact=>{ const assessment=(sourceData?.value.source_times??[]).find(item=>item.artifact_id===artifact.artifact_id); const detailsOpen=artifactDetailsId===artifact.artifact_id; return <article className="detail-card" key={artifact.artifact_id}><h3>{isHttpUrl(artifact.source_uri)?<a href={artifact.source_uri} target="_blank" rel="noreferrer">{sourceCardTitle(artifact.source_uri)}</a>:sourceCardTitle(artifact.source_uri)}</h3>{sourceCardHost(artifact.source_uri)?<p>{sourceCardHost(artifact.source_uri)}</p>:null}<p>{artifact.media_type}</p><p>{cutoffLabel(assessment?.cutoff_state ?? artifact.cutoff_state ?? "UNKNOWN_TIME")}</p><Button small onClick={()=>void citation.ask(artifact.artifact_id)}>{t("workspace.cite")}</Button>{!hosted&&<SourceTimeAdvanced projectId={project.project_id} artifactId={artifact.artifact_id} assessment={assessment} cutoff={sourceData?.value.cutoff_basis}/>} {!hosted&&<><Button small minimal icon={detailsOpen?"chevron-up":"info-sign"} onClick={()=>setArtifactDetailsId(detailsOpen?"":artifact.artifact_id)} aria-expanded={detailsOpen}>{t("workspace.sourceDetails")}</Button><Collapse isOpen={detailsOpen}><RecordInspector value={artifact}/></Collapse></>}</article>})}
    {(sourcesError || sourcesDenied)&&<Callout intent="danger">{sourcesError?.message ?? t("workspace.accessFailed")}</Callout>}
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

/** Model defaults, accounts, language, internet consent, local retrieval, then usage and tools. */
export function SettingsPage({ project, threadId, hosted, workspaceId, storageScope, session, capturedEpoch, status, consent, focusConsent, onConsentFocused, onPage, onOpenCatalog }: {
  project: ProjectSummary; threadId: string; hosted: boolean; workspaceId: string | null; storageScope: BrowserScope; session: ReturnType<typeof useResearchSession>; capturedEpoch: number;
  status: ResearchStatus | undefined; consent?: string; focusConsent: boolean; onConsentFocused: () => void; onPage: (page: Page) => void; onOpenCatalog: () => void;
}) {
  return <><header className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel(hosted?"REVIEW WORKSPACE":"LOCAL WORKSPACE")}</p><h1>{hosted?t("workspace.operations"):t("workspace.settings")}</h1><p>{hosted?t("workspace.reviewInfo"):t("workspace.localToolsInfo")}</p></div></header>
    {hosted ? <section className="detail-card"><h2>{t("workspace.reviewModel")}</h2><p>{t("workspace.operatorModel")}</p></section>
      : <section className="detail-card"><h2>{t("workspace.modelDefaults")}</h2><ModelSettings projectId={project.project_id} threadId={threadId||undefined} selection={session.modelSelection} onSelect={session.chooseModel} onSaved={()=>session.clearModel(session.renderedRevision,capturedEpoch)}/><AutoRetrySwitch projectId={project.project_id}/><HypothesisContractSwitch projectId={project.project_id}/><ModelCredentialPanel projectId={project.project_id} workspaceId={workspaceId ?? undefined}/></section>}
    <LanguageSettingsCard scope={storageScope}/>
    {!hosted && consent !== undefined && <WorkspaceInternetConsent consent={consent} focus={focusConsent} onFocused={onConsentFocused}/>}
    <LocalRetrievalSettingsCard workspaceId={workspaceId ?? "UNBOUND"} projectId={project.project_id} hosted={hosted}/>
    <UsageAndTools project={project} hosted={hosted} status={status} onPage={onPage} onOpenCatalog={onOpenCatalog}/>
  </>;
}
