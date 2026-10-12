import { getLocale } from "../i18n/locale";
import { readDraftLanguage, writeDraftLanguage } from "../api/draftLanguage";
import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Collapse, HTMLSelect, Icon, InputGroup, Tab, Tabs, Tag } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { rpc } from "../api/rpcClient";
import { captureProjectCreation } from "../api/projectCreation";
import { readWorkspaceContext, saveWorkspaceContext } from "../api/research";
import type { WorkspaceReady } from "../api/research";
import { inspectLocalContext, validWorkspaceId, type BrowserScope, type PendingOrigin } from "../api/localWorkspacePersistence";
import { readDraft, writeDraft } from "../api/conversation";
import { fillQuestion, threadForRow, type RowInvestigation } from "./traceInvestigation";
import type { ConnectedArtifact, ProjectSummary, WorkThread } from "../types";
import { CapabilityWorkbench } from "./CapabilityWorkbench";
import { DomainRecords } from "./DomainRecords";
import { ResearchHistoryWorkspace } from "./history/ResearchHistoryWorkspace";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { SourceTimeAdvanced } from "./SourceTimeAdvanced";
import { useCitationGate } from "./SourceCitationGate";
import { cutoffLabel, projectCutoffText } from "../api/sourceTime";
import { FirstRunSetup } from "./FirstRunSetup";
import { WorkspaceInternetConsent } from "./WorkspaceInternetConsent";
import { workspaceSetupIssue, workspaceSetupIssueText, type WorkspaceSetupIssue } from "./workspaceSetupIssue";
import { AutoRetrySwitch } from "./AutoRetrySwitch";
import { HypothesisContractSwitch } from "./HypothesisContractSwitch";
import { ModelCredentialPanel } from "./ModelCredentialPanel";
import { ModelSettings } from "./ModelSettings";
import { ProjectWebSetup } from "./ProjectWebSetup";
import { RecordInspector } from "./RecordInspector";
import { useResearchSession } from "./useResearchSession";
import { isHttpUrl, sourceCardHost, sourceCardTitle } from "../sourceDisplay";
import { eyebrowLabel } from "./statusLabels";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";
import { ConversationTimeline } from "./ConversationTimeline";
import { ArchivedProjects } from "./ArchivedProjects";
import { ResearchComposer } from "./ResearchComposer";
import { ResearchSidePanel, type SidePanelTab } from "./ResearchSidePanel";
import { TracePage } from "./TracePage";
import type { ResearchDetail } from "./ResearchResultCard";
import {
  isTimelineExampleRequested,
  TIMELINE_EXAMPLE_SOURCES,
  timelineExampleStatus,
} from "../api/timelineExample";

type Page = "research" | "resources" | "records" | "trace" | "raw-records" | "settings" | "capabilities";

function TimelineExamplePane() {
  return (
    <section className="workspace-content conversation-page">
      <div className="conversation-layout">
        <div className="conversation-center">
          <header className="conversation-heading">
            <h1>{timelineExampleStatus.problem}</h1>
          </header>
          <div className="conversation-scroll">
            <ConversationTimeline
              projectId=""
              threadId=""
              status={timelineExampleStatus}
              sourceUris={TIMELINE_EXAMPLE_SOURCES}
              onDetail={() => undefined}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function ProjectForm({ onCreated }: { onCreated: (projectId: string) => void }) {
  useTranslation();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [overlay, setOverlay] = useState("general-rnd");
  const [cutoff, setCutoff] = useState(() => { const date = new Date(); return new Date(date.getTime() - date.getTimezoneOffset()*60000).toISOString().slice(0,16); });
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const pending = useRef<(ReturnType<typeof captureProjectCreation> & {signature:string;inFlight:boolean}) | null>(null);
  const create = useMutation({ mutationFn: (captured: ReturnType<typeof captureProjectCreation> & {onCreated:typeof onCreated}) => rpc<ProjectSummary>("project/create",captured.input,captured.key),
    onSuccess: async (response,captured) => {pending.current=null;await client.invalidateQueries({queryKey:["projects"]});captured.onCreated(response.value.project_id);},
    onSettled:()=>{if(pending.current)pending.current.inFlight=false;},
  });
  const submit = () => {
    if (!name.trim() || pending.current?.inFlight) return;
    const signature=JSON.stringify([name,cutoff,overlay]);
    if(pending.current?.signature!==signature)pending.current={...captureProjectCreation(name,cutoff,overlay),signature,inFlight:false};
    pending.current.inFlight=true;
    create.mutate({...pending.current,onCreated});
  };
  return <div className="start-page"><div className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel("RESEARCH WORKSPACE")}</p><h1>{t("workspace.questionEvidence")}</h1><p>{t("workspace.chooseOrCreate")}</p></div><Icon icon="projects" size={50} /></div>
    <form className="onboarding-card" onSubmit={event=>{event.preventDefault();submit();}}><h2>{t("workspace.newProject")}</h2>
      <label>{t("workspace.projectName")}<InputGroup required value={name} onChange={event=>setName(event.target.value)} placeholder={t("workspace.namePlaceholder")} /></label>
      <div className="project-advanced"><Button minimal small icon={advancedOpen?"chevron-up":"chevron-down"} aria-expanded={advancedOpen} onClick={()=>setAdvancedOpen(value=>!value)}>{t("workspace.advanced")}</Button><Collapse isOpen={advancedOpen}><div className="form-row"><label>{t("workspace.cutoff")}<InputGroup required type="datetime-local" value={cutoff} onChange={event=>setCutoff(event.target.value)} /></label>
        <label>{t("workspace.profile")}<InputGroup required value={overlay} onChange={event=>setOverlay(event.target.value)} /></label></div></Collapse></div>
      <p className="muted">{t("workspace.createNotice")}</p>
      <Button type="submit" icon="plus" intent="primary" disabled={!name.trim() || !cutoff || !overlay.trim()} loading={create.isPending}>{t("workspace.create")}</Button>
      {create.error && <Callout intent="danger" role="alert">{create.error.message}</Callout>}</form>
    <div className="start-principles"><div><Icon icon="document-open"/><h3>{t("workspace.sourcesFirst")}</h3><p>{t("workspace.sourceNotice")}</p></div><div><Icon icon="comparison"/><h3>{t("workspace.alternatives")}</h3><p>{t("workspace.alternativesNotice")}</p></div><div><Icon icon="history"/><h3>{t("workspace.historyTitle")}</h3><p>{t("workspace.historyNotice")}</p></div></div>
  </div>;
}

type TraceRowRef = { kind: string; id: string };
/** The trace row a question was started from: held here until it is sent, so the next request carries it. */
type RowOrigin = { threadId: string; origin: PendingOrigin };

function ProjectSession({ project, threadId, epoch, page, onPage, onAdmitted, onUserInput, hosted = false, workspaceId = null, browserScopeId = null, executionReady = true, readSuspended = false, setupIssue = null,
  consent, modelConnected, rowOrigin = null, onOriginDone, onInvestigate, traceFocus = null, onOpenTrace }: {
  project: ProjectSummary; threadId: string; epoch: RefObject<number>; page: Page; onPage: (page: Page) => void; onAdmitted: (threadId: string) => void; onUserInput: () => void;
  hosted?: boolean; workspaceId?: string | null; browserScopeId?: string | null; executionReady?: boolean; readSuspended?: boolean; setupIssue?: WorkspaceSetupIssue | null;
  consent?: string; modelConnected?: boolean;
  rowOrigin?: RowOrigin | null; onOriginDone?: () => void; onInvestigate?: (item: RowInvestigation) => void; traceFocus?: TraceRowRef | null; onOpenTrace?: (row: TraceRowRef) => void;
}) {
  const storageScope = useMemo<BrowserScope>(() => hosted ? { mode: "HOSTED", browserScopeId } : { mode: "LOCAL", workspaceId }, [hosted, workspaceId, browserScopeId]);
  const origin = rowOrigin && rowOrigin.threadId === threadId ? rowOrigin.origin : null;
  const session = useResearchSession(project.project_id, threadId, epoch, onAdmitted, storageScope, executionReady, origin, onOriginDone);
  useEffect(() => { if (origin && page === "research") document.getElementById("live-problem")?.focus(); }, [origin, page]);
  const contentRef = useRef<HTMLElement | null>(null);
  // Once the user has typed or submitted a question here, the work list arriving later must not move them elsewhere.
  const started = session.problem.trim() !== "" || session.submit.isPending;
  useEffect(() => { if (started) onUserInput(); }, [started, onUserInput]);
  useEffect(() => { if (page === "records" && contentRef.current) contentRef.current.scrollTop = 0; }, [page]);
  const [focusConsent,setFocusConsent] = useState(false);
  const clearConsentFocus = useCallback(() => setFocusConsent(false), []);
  const [catalogNamespace,setCatalogNamespace] = useState("all");
  const [detail,setDetail] = useState<ResearchDetail|null>(null);
  const [sideTab,setSideTab] = useState<SidePanelTab>("files");
  const [panelOpen,setPanelOpen] = useState(false);
  const [developerOpen,setDeveloperOpen] = useState(false);
  const [projectDetailsOpen,setProjectDetailsOpen] = useState(false);
  const [artifactDetailsId,setArtifactDetailsId] = useState("");
  const opener = useRef<HTMLElement|null>(null);
  const openDetail = (value:ResearchDetail) => {opener.current=document.activeElement instanceof HTMLElement?document.activeElement:null;setDetail(value);setSideTab(value.kind==="actions"?"actions":value.kind==="hypotheses"?"hypotheses":value.kind==="history"||value.kind==="memory"?"history":value.kind==="evidence"?"evidence":sideTab);setPanelOpen(true);};
  const closeDetail = () => {setDetail(null);setPanelOpen(false);opener.current?.focus();};
  const openResourcePanel = () => {opener.current=document.activeElement instanceof HTMLElement?document.activeElement:null;setDetail(null);setSideTab("files");setPanelOpen(true);};
  const capturedEpoch = epoch.current;
  const citation=useCitationGate(project.project_id);
  const sources = useQuery({queryKey:["sources",project.project_id],queryFn:({signal})=>rpc<{artifacts:ConnectedArtifact[]; source_times?:Array<{artifact_id:string; source_version_id:string; byte_sha256:string; cutoff_state:string; revision:number; assessment_digest:string; metadata_digest?:string; reason_code:string; cutoff_at:string; mode:string}>; cutoff_basis?:{cutoff_at:string; project_revision:number}}>("project/source/list",{project_id:project.project_id},crypto.randomUUID(),signal)});
  const status = session.research.data?.value;
  const queryError = session.research.error ?? sources.error;
  if (readSuspended) return <section className="workspace-content conversation-page">
    {setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} {t("workspace.authorizedRead")}</Callout>}
    {page==="research"&&<ResearchComposer session={session} projectId={project.project_id} threadId={threadId} epoch={capturedEpoch}
      onAttach={openResourcePanel} hosted={hosted} executionReady={false} readSuspended/>}</section>;
  return <section ref={contentRef} className={`workspace-content ${page==="research"?"conversation-page":""}`}>
    {setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} {t("workspace.readOnlyHold")}</Callout>}
    {page==="research" ? <div className={`conversation-layout ${panelOpen?"detail-open":""}`}><div className="conversation-center">
      <header className="conversation-heading"><h1>{status?.problem??(threadId?t("workspace.continue"):t("workspace.newQuestion"))}</h1></header>
      {import.meta.env.VITE_THOTH_TEST_MODE==="true"&&<Callout compact intent="warning" className="test-mode-banner">{t("workspace.testModeNotice")}</Callout>}
      <div className="conversation-scroll"><ConversationTimeline projectId={project.project_id} threadId={threadId} status={status} sourceUris={(sources.data?.value.artifacts??[]).map(item=>item.source_uri)} onDetail={openDetail} queuedInstruction={session.queuedNotice} onContinue={()=>document.getElementById("live-problem")?.focus()} onRetry={question=>{session.setProblem(question);document.getElementById("live-problem")?.focus();}} onResume={operationId=>session.resume.mutate(operationId)} resuming={session.resume.isPending} onOpenHistory={()=>onPage("records")} onOpenTrace={onOpenTrace}/>
        {session.resume.error&&<Callout intent="danger" role="alert">{t("workspace.resumeFailed")} {session.resume.error.message}</Callout>}
        {queryError&&<Callout intent="danger" role="alert">{queryError.message}<Button small onClick={()=>{void session.research.refetch();void sources.refetch();}}>{t("workspace.reload")}</Button></Callout>}
      </div>
      <ResearchComposer session={session} projectId={project.project_id} threadId={threadId} epoch={capturedEpoch} onAttach={openResourcePanel}
        onOpenSettings={target=>{setFocusConsent(target==="consent");onPage("settings");}} hosted={hosted} executionReady={executionReady}
        consentMissing={!hosted && consent==="UNDECIDED"} modelConnected={modelConnected}/>
    </div>{panelOpen&&<ResearchSidePanel tab={sideTab} onTab={setSideTab} detail={detail} onClose={closeDetail} sources={sources.data?.value.artifacts??[]} project={project} thread={{thread_id:threadId,problem:status?.problem??t("workspace.research"),current_object_ids:status?.current_object_ids??[]}} onOpenRecords={()=>onPage("records")} onManageResources={()=>{closeDetail();onPage("resources");}}/>}</div> :
    page==="capabilities" && !hosted ? <><Button minimal icon="arrow-left" onClick={()=>onPage("settings")}>{t("workspace.backSettings")}</Button><CapabilityWorkbench projectId={project.project_id} threadId={threadId} initialNamespace={catalogNamespace}/></> :
    page==="trace" ? <TracePage projectId={project.project_id} responseLanguage={session.responseLanguage} onInvestigate={onInvestigate} focusRow={traceFocus}/> : page==="records" ? <ResearchHistoryWorkspace key={`${project.project_id}:${threadId}`} projectId={project.project_id} threadId={threadId}
      onUseQuestion={session.problem.trim() ? undefined : question=>{session.setProblem(question);onPage("research");}}/> :
    page==="raw-records" && !hosted ? <><Button minimal icon="arrow-left" onClick={()=>onPage("settings")}>{t("workspace.backSettings")}</Button><DomainRecords key={`${project.project_id}:${threadId}`} projectId={project.project_id} threadId={threadId} operationId={status?.request?.operation_id} onOpenCatalog={namespace=>{setCatalogNamespace(namespace);onPage("capabilities");}}/></> :
    page==="resources" ? <><header className="workspace-heading"><div><h1>{t("workspace.resources")}</h1><p>{t("workspace.resourcesNotice")}</p></div><Button minimal icon="arrow-left" onClick={()=>onPage("research")}>{t("workspace.backConversation")}</Button></header><ProjectWebSetup projectId={project.project_id}/><FileConnectionPanel projectId={project.project_id} hosted={hosted}/><h2>{t("workspace.connectedSources")}</h2>
      {citation.dialog}{citation.message&&<Callout compact>{citation.message}</Callout>}{(sources.data?.value.artifacts??[]).map(artifact=>{ const assessment=(sources.data?.value.source_times??[]).find(item=>item.artifact_id===artifact.artifact_id); const detailsOpen=artifactDetailsId===artifact.artifact_id; return <article className="detail-card" key={artifact.artifact_id}><h3>{isHttpUrl(artifact.source_uri)?<a href={artifact.source_uri} target="_blank" rel="noreferrer">{sourceCardTitle(artifact.source_uri)}</a>:sourceCardTitle(artifact.source_uri)}</h3>{sourceCardHost(artifact.source_uri)?<p>{sourceCardHost(artifact.source_uri)}</p>:null}<p>{artifact.media_type}</p><p>{cutoffLabel(assessment?.cutoff_state ?? artifact.cutoff_state ?? "UNKNOWN_TIME")}</p><Button small onClick={()=>void citation.ask(artifact.artifact_id)}>{t("workspace.cite")}</Button>{!hosted&&<SourceTimeAdvanced projectId={project.project_id} artifactId={artifact.artifact_id} assessment={assessment} cutoff={sources.data?.value.cutoff_basis}/>} {!hosted&&<><Button small minimal icon={detailsOpen?"chevron-up":"info-sign"} onClick={()=>setArtifactDetailsId(detailsOpen?"":artifact.artifact_id)} aria-expanded={detailsOpen}>{t("workspace.sourceDetails")}</Button><Collapse isOpen={detailsOpen}><RecordInspector value={artifact}/></Collapse></>}</article>})}
      {sources.error&&<Callout intent="danger">{sources.error.message}</Callout>}
    </> : <><header className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel(hosted?"REVIEW WORKSPACE":"LOCAL WORKSPACE")}</p><h1>{hosted?t("workspace.operations"):t("workspace.settings")}</h1><p>{hosted?t("workspace.reviewInfo"):t("workspace.localToolsInfo")}</p></div></header>
      {hosted ? (
        <section className="detail-card"><h2>{t("workspace.reviewModel")}</h2><p>{t("workspace.operatorModel")}</p></section>
      ) : (
        <section className="detail-card"><h2>{t("workspace.modelDefaults")}</h2><ModelSettings projectId={project.project_id} threadId={threadId||undefined} selection={session.modelSelection} onSelect={session.chooseModel} onSaved={()=>session.clearModel(session.renderedRevision,capturedEpoch)}/><AutoRetrySwitch projectId={project.project_id}/><HypothesisContractSwitch projectId={project.project_id}/><ModelCredentialPanel projectId={project.project_id} workspaceId={workspaceId ?? undefined}/></section>
      )}
      <section className="detail-card"><h2>{t("language.settingsTitle")}</h2><p>{t("language.fixed")}</p><p>{t("language.cost")}</p></section>
      {!hosted && consent !== undefined && <WorkspaceInternetConsent consent={consent} focus={focusConsent} onFocused={clearConsentFocus}/>}
      <section className="detail-card"><h2>{t("workspace.usage")}</h2><p>{message("reportedTokens", status?.usage?.total_tokens?.toLocaleString(getLocale() === "en" ? "en-US" : "ko-KR")??t("workspace.noUsage"), status?.usage?.state==="PARTIAL"?t("workspace.partialUsage"):"")}</p><p>{t("workspace.usagePending")}</p>{!hosted&&<><Button small minimal icon={projectDetailsOpen?"chevron-up":"settings"} onClick={()=>setProjectDetailsOpen(value=>!value)} aria-expanded={projectDetailsOpen}>{t("workspace.projectSettings")}</Button><Collapse isOpen={projectDetailsOpen}><RecordInspector value={project}/></Collapse></>}</section>
      <section className="detail-card"><h2>{hosted?t("workspace.recordsSources"):t("workspace.recordsTools")}</h2><Button minimal icon="folder-open" onClick={()=>onPage("resources")}>{t("workspace.manageSources")}</Button><Button minimal icon="history" onClick={()=>onPage("records")}>{t("workspace.history")}</Button>{!hosted&&<><Button minimal icon={developerOpen?"chevron-up":"chevron-down"} onClick={()=>setDeveloperOpen(value=>!value)} aria-expanded={developerOpen}>{t("workspace.devTools")}</Button><Collapse isOpen={developerOpen}><div className="local-developer-tools"><Button minimal icon="database" onClick={()=>onPage("raw-records")}>{t("workspace.detailedRecords")}</Button><Button minimal icon="code" onClick={()=>{setCatalogNamespace("all");onPage("capabilities");}}>{t("workspace.apiTools")}</Button><RecordInspector value={{request:status?.request,attempt:status?.attempt,budget:status?.budget,usage:status?.usage,cleanup:status?.cleanup}}/></div></Collapse></>}</section>
    </>}
  </section>;
}

function ProjectNavItem({ project, active, removing, onOpen, onRemove }: {
  project: ProjectSummary;
  active: boolean;
  removing: boolean;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const [menuOpen,setMenuOpen] = useState(false);
  return <div className={`project-nav-row ${active ? "active" : ""}`} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setMenuOpen(false);}}>
    <Button minimal alignText="left" className={`project-button ${active ? "active" : ""}`} onClick={onOpen}>
      <Icon icon={active ? "folder-open" : "folder-close"}/><span><strong>{project.name}</strong></span>
    </Button>
    <Button small minimal icon="more" aria-label={message("projectMenu", project.name)} aria-expanded={menuOpen} loading={removing} onClick={()=>setMenuOpen(value=>!value)}/>
    {menuOpen&&<div className="project-inline-menu" role="menu"><Button fill alignText="left" minimal role="menuitem" icon="trash" intent="danger" disabled={removing} onClick={()=>{setMenuOpen(false);onRemove();}}>{t("workspace.removeProject")}</Button></div>}
  </div>;
}

export function ProjectLanding({ projects, loading = false, contextRestoreIssue = null, setupIssue = null, onOpen, onCreated, openCreate = false, onOpenCreateHandled }: {
  projects: ProjectSummary[];
  loading?: boolean;
  contextRestoreIssue?: "CORRUPT" | "UNAVAILABLE" | null;
  setupIssue?: WorkspaceSetupIssue | null;
  onOpen: (projectId: string) => void;
  onCreated: (projectId: string) => void; openCreate?: boolean; onOpenCreateHandled?: () => void;
}) {
  useTranslation();
  const [creating,setCreating] = useState(false);
  useEffect(()=>{if(!loading&&projects.length===0)setCreating(true);},[loading,projects.length]);
  useEffect(()=>{if(openCreate){setCreating(true);onOpenCreateHandled?.();}},[openCreate,onOpenCreateHandled]);
  const restoreNotice = contextRestoreIssue && <Callout compact intent="warning" role="alert">{contextRestoreIssue === "CORRUPT"
    ? t("workspace.contextCorrupt")
    : t("workspace.contextUnavailable")}
    {" "}{t("workspace.restoreScope")}</Callout>;
  const setupNotice = setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} {t("workspace.existingRead")}</Callout>;
  if (creating) return <>{setupNotice}{restoreNotice}<Button minimal icon="arrow-left" disabled={projects.length===0} onClick={()=>setCreating(false)}>{t("workspace.backProjects")}</Button><ProjectForm onCreated={onCreated}/></>;
  return <div className="start-page project-home">{setupNotice}{restoreNotice}<div className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel("RESEARCH WORKSPACE")}</p><h1>{t("workspace.continueHeading")}</h1><p>{t("workspace.openOrCreate")}</p></div><Icon icon="projects" size={50}/></div>
    <section className="project-home-list" aria-label={t("workspace.continueProject")}><div className="section-title-row"><h2>{t("workspace.projects")}</h2><Button icon="plus" intent="primary" onClick={()=>setCreating(true)}>{t("workspace.newProject")}</Button></div>
      {loading&&<p className="empty-copy" role="status">{t("workspace.loadingProjects")}</p>}
      {projects.map(project=><Button fill large alignText="left" className="project-home-item" key={project.project_id} icon="folder-open" onClick={()=>onOpen(project.project_id)}><span><strong>{project.name}</strong><small>{t("workspace.openProject")}</small></span></Button>)}
    </section>
    <p className="muted">{t("workspace.noReplacement")}</p>
  </div>;
}

export function LiveProjectWorkspace() {
  useTranslation();
  const [context, setContext] = useState({ projectId: "", threadId: "" });
  const [restoredFor, setRestoredFor] = useState<string | null>(null);
  const [contextStoreFailed, setContextStoreFailed] = useState(false);
  const [contextRestoreIssue, setContextRestoreIssue] = useState<"CORRUPT" | "UNAVAILABLE" | null>(null);
  const [missingSelection, setMissingSelection] = useState<"project" | "thread" | null>(null);
  const [page, setPage] = useState<Page>("research");
  const [search, setSearch] = useState("");
  const [selectionVersion,setSelectionVersion] = useState(0);
  const [rowOrigin,setRowOrigin] = useState<RowOrigin|null>(null);
  const [traceFocus,setTraceFocus] = useState<TraceRowRef|null>(null);
  const [openCreate,setOpenCreate] = useState(false);
  const epoch = useRef(0);
  const previousScope = useRef<string | null>(null);
  const pendingAdmission = useRef<string | null>(null);
  const openLatestWork = useRef(false);
  const renderedEpoch = epoch.current;
  const client = useQueryClient();
  const readyQuery = useQuery({queryKey:["workspace-ready"],queryFn:({signal})=>rpc<WorkspaceReady>("workspace/ready",{}, "ws-ready", signal)});
  const ready = readyQuery.error ? undefined : readyQuery.data?.value;
  const setupIssue = workspaceSetupIssue(ready);
  const hosted = ready?.deployment_mode === "HOSTED_REVIEW";
  const browserScopeId = ready?.browser_scope_id ?? null;
  const workspaceId = !hosted && validWorkspaceId(ready?.workspace_id) ? ready.workspace_id : null;
  const storageScope = useMemo<BrowserScope>(() => hosted ? { mode: "HOSTED", browserScopeId } : { mode: "LOCAL", workspaceId }, [hosted, workspaceId, browserScopeId]);
  const scopeKey = ready ? hosted ? `HOSTED:${browserScopeId ?? "LEGACY"}` : `LOCAL:${workspaceId ?? "UNBOUND"}` : null;
  const projectsQuery = useQuery({queryKey:["projects",scopeKey],enabled:Boolean(scopeKey),queryFn:({signal})=>rpc<{projects:ProjectSummary[]}>("project/list",{project_id:"system:projects"},crypto.randomUUID(),signal)});
  const projects = useMemo(() => projectsQuery.error ? [] : projectsQuery.data?.value.projects ?? [],
    [projectsQuery.error, projectsQuery.data]);
  const project = projects.find(item=>item.project_id===context.projectId);
  const threadsQuery = useQuery({queryKey:["threads",scopeKey,context.projectId],enabled:Boolean(scopeKey && project),queryFn:({signal})=>rpc<{threads:WorkThread[]}>("thread/list",{project_id:context.projectId},crypto.randomUUID(),signal)});
  const threads = useMemo(() => threadsQuery.error ? [] : threadsQuery.data?.value.threads ?? [],
    [threadsQuery.error, threadsQuery.data]);
  const visibleProjects = projectsQuery.isFetching ? [] : projects;
  const visibleThreads = threadsQuery.isFetching ? [] : threads;
  const health = useQuery({queryKey:["health"],queryFn:async()=>{const response=await fetch("/healthz",{credentials:"same-origin"});if(!response.ok)throw new Error(`HTTP ${response.status}`);return await response.json() as {status:string;version:string};},retry:false,refetchInterval:30000});
  useEffect(() => {
    if (!scopeKey || restoredFor === scopeKey) return;
    if (previousScope.current !== scopeKey) {
      client.removeQueries({ predicate: query => !["workspace-ready", "health"].includes(String(query.queryKey[0])) && !query.queryKey.includes(scopeKey) });
    }
    previousScope.current = scopeKey;
    const restored = storageScope.mode === "LOCAL" ? inspectLocalContext(storageScope.workspaceId) : null;
    setContext(restored?.context ?? readWorkspaceContext(storageScope));
    setContextRestoreIssue(restored?.kind === "CORRUPT" || restored?.kind === "UNAVAILABLE" ? restored.kind : null);
    setContextStoreFailed(false);
    setRestoredFor(scopeKey);
    setMissingSelection(null);
    setPage("research");
    epoch.current += 1;
    setSelectionVersion(value => value + 1);
  }, [scopeKey, restoredFor, storageScope, client]);
  useEffect(() => {
    if (!scopeKey || restoredFor !== scopeKey || contextRestoreIssue) return;
    const saved = saveWorkspaceContext(context.projectId, context.threadId, storageScope);
    if (storageScope.mode === "LOCAL") setContextStoreFailed(!saved);
  }, [scopeKey, restoredFor, context, storageScope, contextRestoreIssue]);
  useEffect(() => {
    if (!scopeKey || restoredFor !== scopeKey) return;
    if (context.projectId && projectsQuery.isSuccess && !projectsQuery.isFetching && !projects.some(item => item.project_id === context.projectId)) {
      client.removeQueries({ predicate: query => query.queryKey.includes(context.projectId) });
      epoch.current += 1; pendingAdmission.current = null; setSelectionVersion(value => value + 1);
      setContext({ projectId: "", threadId: "" }); setMissingSelection("project"); return;
    }
    if (pendingAdmission.current && threads.some(item => item.thread_id === pendingAdmission.current)) pendingAdmission.current = null;
    if (context.threadId && project && threadsQuery.isSuccess && !threadsQuery.isFetching &&
        !threads.some(item => item.thread_id === context.threadId) && pendingAdmission.current !== context.threadId) {
      client.removeQueries({ predicate: query => query.queryKey.includes(context.threadId) });
      epoch.current += 1; pendingAdmission.current = null; setSelectionVersion(value => value + 1);
      setContext({ projectId: context.projectId, threadId: "" }); setMissingSelection("thread");
    }
  }, [scopeKey, restoredFor, context, projects, project, projectsQuery.isSuccess, projectsQuery.isFetching, threads, threadsQuery.isSuccess, threadsQuery.isFetching, client]);
  const chooseProject = (projectId:string, nextPage:Page="research") => {setRowOrigin(null);setTraceFocus(null);epoch.current+=1;pendingAdmission.current=null;setMissingSelection(null);setContextRestoreIssue(null);setSelectionVersion(v=>v+1);setContext({projectId,threadId:""});setPage(projectId?nextPage:"research");};
  const openProject = (projectId:string) => {
    if (projectId===context.projectId) {setPage("research");return;}
    chooseProject(projectId);
    openLatestWork.current = Boolean(projectId);
  };
  const chooseThread = (threadId:string) => {setRowOrigin(null);openLatestWork.current=false;epoch.current+=1;pendingAdmission.current=null;setMissingSelection(null);setContextRestoreIssue(null);setSelectionVersion(v=>v+1);setContext(current=>({...current,threadId}));setPage("research");};
  const cancelOpenLatest = useCallback(() => { openLatestWork.current = false; }, []);
  // "원인 조사" on a trace row: open the conversation of that row (a new one when it has none) with the question filled in.
  // Nothing is sent; the user sends it, and the request then carries the row.
  const investigate = (item: RowInvestigation) => {
    if (!project) return;
    const target = threadForRow(threads, item.origin.subject_kind, item.origin.subject_id)?.thread_id ?? "";
    const draft = readDraft(project.project_id, target, storageScope);
    const priorLanguage = readDraftLanguage(storageScope, project.project_id, target);
    const language = draft && priorLanguage !== undefined ? priorLanguage : item.responseLanguage;
    const question = language !== undefined && item.questionForLanguage ? item.questionForLanguage(language) : item.question;
    if (language !== undefined) writeDraftLanguage(storageScope, project.project_id, target, language);
    writeDraft(project.project_id, target, fillQuestion(draft, question), storageScope);
    chooseThread(target);
    setRowOrigin({ threadId: target, origin: item.origin });
  };
  const openTraceRow = (row: TraceRowRef) => { setTraceFocus(row); setPage("trace"); };
  // Opening a project lands on its most recently updated work instead of an empty question screen.
  useEffect(() => {
    if (!openLatestWork.current || !project || context.threadId || !threadsQuery.isSuccess || threadsQuery.isFetching) return;
    openLatestWork.current = false;
    const latest = threads.reduce<WorkThread | null>((best, item) => !best || (item.updated_at ?? "") > (best.updated_at ?? "") ? item : best, null);
    if (latest) chooseThread(latest.thread_id);
  }, [project, context.threadId, threads, threadsQuery.isSuccess, threadsQuery.isFetching]);
  const archiveProject = useMutation({
    mutationFn: (target: ProjectSummary) => rpc<ProjectSummary>("project/archive", {project_id: target.project_id, expected_revision: target.revision}, crypto.randomUUID()),
    onSuccess: async (_response, target) => {
      await client.invalidateQueries({queryKey:["projects"]});
      await client.invalidateQueries({queryKey:["threads", target.project_id]});
      if (context.projectId === target.project_id) chooseProject("");
    },
  });
  const removeProject = (target: ProjectSummary) => {
    if (!window.confirm(message("archiveProject", target.name))) return;
    archiveProject.mutate(target);
  };
  if (readyQuery.isPending || (readyQuery.isSuccess && !ready)) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">{t("workspace.preparing")}</p></main>;
  }
  if (readyQuery.error) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="danger" title={t("workspace.readinessFailed")}>{t("workspace.readinessAdvice")}<Button small onClick={()=>void readyQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout></section></main>;
  }
  if (!ready || !scopeKey || restoredFor !== scopeKey) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">{t("workspace.loadingSaved")}</p></main>;
  }
  if (hosted && !ready.ready) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  if (projectsQuery.isPending && !projectsQuery.data) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">{t("workspace.checkingProjects")}</p></main>;
  }
  if (projectsQuery.error) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="warning" role="alert">{t("workspace.accessFailed")}<Button small onClick={()=>void projectsQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout></section></main>;
  }
  const workspaceReadable = hosted || ready.workspace_readable !== false;
  const setupComplete = ready.setup_complete ?? (ready.setup?.internet_consent !== undefined && ready.setup.internet_consent !== "UNDECIDED");
  const executionReady = hosted ? ready.ready : (ready.execution_ready ?? ready.ready);
  if (!workspaceReadable) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="warning" role="alert">{t("workspace.savedUnavailable")}<Button small onClick={()=>void readyQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout></section></main>;
  }
  if (!hosted && ready.workspace_readable === undefined && !ready.ready) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  if (!hosted && !setupComplete && projects.length === 0) {
    return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  }
  if (context.threadId && project && threadsQuery.isPending && !threadsQuery.data) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">{t("workspace.checkingAccess")}</p></main>;
  }
  if (context.threadId && project && threadsQuery.error) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="warning" role="alert">{t("workspace.taskUnavailable")}<Button small onClick={()=>void threadsQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout></section></main>;
  }
  if (context.threadId && project && !threads.some(item=>item.thread_id===context.threadId) && pendingAdmission.current!==context.threadId) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">{t("workspace.selectionCheck")}</p></main>;
  }
  return <main className="workstation conversation-workstation bp6-dark">
    <aside className="navigation-rail"><div className="brand"><div className="brand-mark"><Icon icon="layout-grid" size={20}/></div><div><strong>THOTH</strong><span>RESEARCH WORKSTATION</span></div><Tag minimal>{hosted ? "REVIEW" : "LOCAL"}</Tag></div>
      <Button icon="plus" intent="primary" className="new-project-button" onClick={()=>{chooseProject("");setOpenCreate(true);}}>{t("workspace.newProject")}</Button>
      <InputGroup small leftIcon="search" aria-label={t("workspace.searchProjects")} placeholder={t("workspace.searchPlaceholder")} value={search} onChange={event=>setSearch(event.target.value)}/>
      <section className="nav-section"><div className="nav-section-heading"><p className="nav-label">PROJECTS</p><Button small minimal icon="refresh" aria-label={t("workspace.refreshProjects")} onClick={()=>void projectsQuery.refetch()}/></div>
        {visibleProjects.filter(item=>`${item.name} ${item.project_id}`.toLowerCase().includes(search.toLowerCase())).flatMap(item=>[
          <ProjectNavItem project={item} active={item.project_id===context.projectId} removing={archiveProject.isPending} key={item.project_id} onOpen={()=>openProject(item.project_id)} onRemove={()=>removeProject(item)}/>,
          item.project_id===context.projectId ? <div className="project-sessions" key={`${item.project_id}-sessions`}>
            <Button small minimal icon="plus" className="new-session-button" aria-label={t("workspace.newTask")} onClick={()=>chooseThread("")}>{t("workspace.newSession")}</Button>
            {archiveProject.error&&<Callout compact intent="danger">{archiveProject.error.message}</Callout>}
            {threadsQuery.isPending&&<p className="empty-copy">{t("workspace.loadingSessions")}</p>}
            {threadsQuery.error&&<Callout compact intent="danger">{threadsQuery.error.message}</Callout>}
            {visibleThreads.map(thread=><Button minimal alignText="left" className={`thread-button ${thread.thread_id===context.threadId?"active":""}`} key={thread.thread_id} onClick={()=>chooseThread(thread.thread_id)}><Icon icon="chat"/><span><strong>{thread.problem||t("workspace.untitledSession")}</strong><small>{thread.execution_state==="RUNNING"?t("workspace.running"):thread.execution_state==="PAUSED"?t("workspace.paused"):t("workspace.reopen")}</small></span></Button>)}
            {!threadsQuery.isPending&&!threadsQuery.error&&threads.length===0&&<p className="empty-copy">{t("workspace.firstQuestion")}</p>}
          </div> : null,
        ])}
        {!projectsQuery.isPending&&!projectsQuery.error&&projects.length===0&&<p className="empty-copy">{t("workspace.noProjects")}</p>}<ArchivedProjects scopeKey={scopeKey}/>
      </section>
      <div className="sidebar-bottom"><Button minimal alignText="left" icon={hosted?"info-sign":"settings"} onClick={()=>setPage("settings")}>{hosted?t("workspace.operations"):t("workspace.settingsTools")}</Button></div>
    </aside>
    <header className="workstation-topbar"><div className="context-path"><Icon icon="projects"/><span>Workspace</span>{import.meta.env.VITE_THOTH_TEST_MODE==="true"&&<Tag intent="warning">{t("workspace.testMode")}</Tag>}<span>/</span><strong>{projectsQuery.isFetching?t("workspace.checkingProject"):project?.name??t("workspace.selectProject")}</strong>{context.threadId&&<><span>/</span><small>{threadsQuery.isFetching?t("workspace.checkingTask"):visibleThreads.find(item=>item.thread_id===context.threadId)?.problem??t("workspace.selectedTask")}</small></>}</div>
      <div className="header-status">{project&&projectCutoffText(project.cutoff_at)&&<Tag minimal icon="time" title={t("workspace.cutoffHint")}>{message("cutoff", projectCutoffText(project.cutoff_at))}</Tag>}<Tag minimal intent={health.data?.status==="ok"?"success":health.error?"danger":"none"}>{health.data?.status==="ok"?t("workspace.serverConnected"):health.error?t("workspace.serverFailed"):t("workspace.checkingConnection")}</Tag>{!hosted&&!executionReady&&<Tag minimal intent="warning">{t("workspace.setupNeeded")}</Tag>}{!hosted&&contextStoreFailed&&<Tag minimal intent="warning">{t("workspace.locationUnsaved")}</Tag>}{missingSelection&&<Tag minimal intent="warning">{message("missingSelection", missingSelection)}</Tag>}<Button small minimal icon="refresh" aria-label={t("workspace.refreshContext")} onClick={()=>void client.invalidateQueries()}/></div></header>
    <div className="workspace-navigation"><Tabs id="workspace-tabs" selectedTabId={page} onChange={value=>setPage(value as Page)}><Tab id="research" title={t("workspace.conversation")}/><Tab id="resources" title={t("workspace.resources")} disabled={!project}/><Tab id="records" title={t("workspace.history")} disabled={!project}/><Tab id="trace" title={t("workspace.trace")} disabled={!project}/><Tab id="settings" title={t("workspace.settings")}/></Tabs>
      {project&&<div className="context-policy"><HTMLSelect aria-label={t("workspace.chooseTask")} disabled={threadsQuery.isFetching} value={context.threadId} onChange={event=>chooseThread(event.target.value)} options={[{value:"",label:t("workspace.newTask")},...visibleThreads.map(item=>({value:item.thread_id,label:(item.problem||t("workspace.untitledTask")).slice(0,40)}))]}/></div>}</div>
    {project ? <WorkspaceErrorBoundary resetKey={`${scopeKey}:${project.project_id}:${context.threadId}:${page}`} onRetry={()=>client.invalidateQueries()}><ProjectSession key={`${scopeKey}:${project.project_id}:${selectionVersion}`} project={project} threadId={context.threadId} epoch={epoch} page={page} onPage={setPage} hosted={hosted} workspaceId={workspaceId} browserScopeId={browserScopeId} executionReady={executionReady && !threadsQuery.error} consent={ready.setup?.internet_consent} modelConnected={ready.model_connected}
      readSuspended={readyQuery.isFetching || projectsQuery.isFetching || threadsQuery.isFetching} setupIssue={setupIssue}
      onUserInput={cancelOpenLatest} onAdmitted={threadId=>{pendingAdmission.current=threadId;setContext(current=>({...current,threadId}));}}
      rowOrigin={rowOrigin} onOriginDone={()=>setRowOrigin(null)} onInvestigate={investigate} traceFocus={traceFocus} onOpenTrace={openTraceRow}/></WorkspaceErrorBoundary> :
      isTimelineExampleRequested() && page === "research" ? <TimelineExamplePane /> :
      <section className="workspace-content">{missingSelection&&<Callout intent="warning" role="status">{missingSelection==="project"?t("workspace.missingProject"):t("workspace.taskSelectionNeeded")} {t("workspace.draftNotSent")}</Callout>}{contextStoreFailed&&!hosted&&<Callout intent="warning" role="status">{t("workspace.locationFailure")}</Callout>}{!hosted&&!validWorkspaceId(workspaceId)&&<Callout intent="warning" role="status">{t("workspace.identityMissing")}</Callout>}{page==="settings"?<>{hosted?<Callout icon="info-sign" title={t("workspace.chooseProjectNotice")}>{t("workspace.selectForInfo")}</Callout>:<section className="detail-card"><h2>{t("workspace.connectModel")}</h2><ModelCredentialPanel projectId="system:workspace" workspaceId={workspaceId ?? undefined}/><p className="muted">{t("workspace.selectForDefaults")}</p></section>}</>:<ProjectLanding openCreate={openCreate} onOpenCreateHandled={()=>setOpenCreate(false)} contextRestoreIssue={contextRestoreIssue} setupIssue={setupIssue} projects={visibleProjects} loading={projectsQuery.isPending||projectsQuery.isFetching} onOpen={openProject} onCreated={projectId=>{if(renderedEpoch===epoch.current)chooseProject(projectId,"resources");}}/>}</section>}
  </main>;
}

