import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Collapse, Icon, InputGroup, PopoverNext, Tab, Tabs, Tag } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { rpc } from "../api/rpcClient";
import { rereadProjects } from "../api/projectActionFailure";
import { ProjectActionNotice } from "./ProjectActionNotice";
import { captureProjectCreation } from "../api/projectCreation";
import type { WorkspaceReady } from "../api/research";
import { validWorkspaceId, type BrowserScope } from "../api/localWorkspacePersistence";
import type { RowInvestigation } from "./traceInvestigation";
import type { ProjectSummary, WorkThread } from "../types";
import { CapabilityWorkbench } from "./CapabilityWorkbench";
import { DomainRecords } from "./DomainRecords";
import { ResearchHistoryWorkspace } from "./history/ResearchHistoryWorkspace";
import { useCitationGate } from "./SourceCitationGate";
import { projectCutoffText } from "../api/sourceTime";
import { WorkspaceLocationNotice } from "./WorkspaceLocationNotice";
import { workspaceSetupIssue, workspaceSetupIssueText, type WorkspaceSetupIssue } from "./workspaceSetupIssue";
import { ModelCredentialPanel } from "./ModelCredentialPanel";
import { LanguageSettingsCard } from "./LanguageSettingsCard";
import { useResearchSession } from "./useResearchSession";
import { useReadAccessProtection } from "./useReadAccessProtection";
import { eyebrowLabel } from "./statusLabels";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";
import { ConversationTimeline } from "./ConversationTimeline";
import { ArchivedProjects } from "./ArchivedProjects";
import { ResearchComposer } from "./ResearchComposer";
import { ResearchSidePanel, type SidePanelTab } from "./ResearchSidePanel";
import { TracePage } from "./TracePage";
import type { ResearchDetail } from "./ResearchResultCard";
import { ProjectReadinessCard, type ReadinessFacts } from "./ProjectReadinessCard";
import { ResourcesPage, SettingsPage } from "./ProjectSessionPages";
import { useProjectSources } from "./useProjectSources";
import { executionReadiness, workspaceGate } from "./WorkspaceGate";
import { useSelectionReconcile, useWorkspaceSelection, type Page, type RowOrigin, type TraceRowRef } from "./workspaceSelection";
import {
  isTimelineExampleRequested,
  TIMELINE_EXAMPLE_SOURCES,
  timelineExampleStatus,
} from "../api/timelineExample";

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
  const detailFromResearch = useRef(false);
  useEffect(() => {
    if (session.researchReadDenied && detailFromResearch.current) { detailFromResearch.current = false; setDetail(null); setPanelOpen(false); }
  }, [session.researchReadDenied]);
  const [sideTab,setSideTab] = useState<SidePanelTab>("files");
  const [panelOpen,setPanelOpen] = useState(false);
  const opener = useRef<HTMLElement|null>(null);
  const openDetail = (value:ResearchDetail) => {detailFromResearch.current = value.result === session.research.data?.value.current_result?.result || value.result === session.research.data?.value.previous_result?.result;opener.current=document.activeElement instanceof HTMLElement?document.activeElement:null;setDetail(value);setSideTab(value.kind==="actions"?"actions":value.kind==="hypotheses"?"hypotheses":value.kind==="history"||value.kind==="memory"?"history":value.kind==="evidence"?"evidence":sideTab);setPanelOpen(true);};
  const closeDetail = () => {setDetail(null);setPanelOpen(false);opener.current?.focus();};
  const openResourcePanel = () => {opener.current=document.activeElement instanceof HTMLElement?document.activeElement:null;setDetail(null);setSideTab("files");setPanelOpen(true);};
  const capturedEpoch = epoch.current;
  const citation=useCitationGate(project.project_id, session.isCurrentScope);
  const sources = useProjectSources(project.project_id);
  const sourcesDenied = useReadAccessProtection(["sources", project.project_id], sources.error);
  const sourceData = sourcesDenied ? undefined : sources.data;
  const status = session.research.data?.value;
  const queryError = session.research.error ?? sources.error;
  const openSettings = (target?: "consent") => { setFocusConsent(target === "consent"); onPage("settings"); };
  // A thread with nothing sent yet: the readiness card carries the setup notice, so the composer does not repeat it.
  const freshConversation = !threadId && !status?.request && status?.operation_state !== "RUNNING";
  const readiness: ReadinessFacts = { sourceCount: sourceData ? sourceData.value.artifacts.length : null, modelConnected, consent, cutoffAt: project.cutoff_at, executionReady, hosted };
  if (readSuspended) return <section className="workspace-content conversation-page">
    {setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} {t("workspace.authorizedRead")}</Callout>}
    {page==="research"&&<ResearchComposer session={session} projectId={project.project_id} threadId={threadId} epoch={capturedEpoch}
      onAttach={openResourcePanel} hosted={hosted} executionReady={false} readSuspended/>}</section>;
  return <section ref={contentRef} className={`workspace-content ${page==="research"?"conversation-page":""}`}>
    {setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} {t("workspace.readOnlyHold")}</Callout>}
    {page==="research" ? <div className={`conversation-layout ${panelOpen?"detail-open":""}`}><div className="conversation-center">
      <header className="conversation-heading"><h1>{status?.problem??(threadId?t("workspace.continue"):t("workspace.newQuestion"))}</h1></header>
      {import.meta.env.VITE_THOTH_TEST_MODE==="true"&&<Callout compact intent="warning" className="test-mode-banner">{t("workspace.testModeNotice")}</Callout>}
      <div className="conversation-scroll"><ConversationTimeline projectId={project.project_id} threadId={threadId} status={status} sourceUris={(sourceData?.value.artifacts??[]).map(item=>item.source_uri)} onDetail={openDetail} queuedInstruction={session.queuedNotice} onContinue={()=>document.getElementById("live-problem")?.focus()} onRetry={question=>{session.setProblem(question);document.getElementById("live-problem")?.focus();}} onResume={operationId=>session.resume.mutate(operationId)} resuming={session.resume.isPending} onOpenHistory={()=>onPage("records")} onOpenSettings={()=>openSettings()} onOpenTrace={onOpenTrace}
        emptyNotice={<ProjectReadinessCard facts={readiness} onOpenSettings={openSettings} onManageSources={()=>onPage("resources")}/>}/>
        {session.resume.error&&<Callout intent="danger" role="alert">{t("workspace.resumeFailed")} {session.resume.error.message}</Callout>}
        {(queryError || sourcesDenied || session.researchReadDenied)&&<Callout intent="danger" role="alert">{queryError?.message ?? t("workspace.accessFailed")}<Button small onClick={()=>{void session.research.refetch();void sources.refetch();}}>{t("workspace.reload")}</Button></Callout>}
      </div>
      <ResearchComposer session={session} projectId={project.project_id} threadId={threadId} epoch={capturedEpoch} onAttach={openResourcePanel}
        onOpenSettings={openSettings} hosted={hosted} executionReady={executionReady} setupNoticeShown={freshConversation}
        consentMissing={!hosted && consent==="UNDECIDED"} modelConnected={modelConnected}/>
    </div>{panelOpen && !(session.researchReadDenied && detailFromResearch.current)&&<ResearchSidePanel tab={sideTab} onTab={setSideTab} detail={detail} onClose={closeDetail} sources={sourceData?.value.artifacts??[]} project={project} thread={{thread_id:threadId,problem:status?.problem??t("workspace.research"),current_object_ids:status?.current_object_ids??[]}} onOpenRecords={()=>onPage("records")} onManageResources={()=>{closeDetail();onPage("resources");}}/>}</div> :
    page==="capabilities" && !hosted ? <><Button minimal icon="arrow-left" onClick={()=>onPage("settings")}>{t("workspace.backSettings")}</Button><CapabilityWorkbench projectId={project.project_id} threadId={threadId} initialNamespace={catalogNamespace}/></> :
    page==="trace" ? <TracePage projectId={project.project_id} responseLanguage={session.responseLanguage} onInvestigate={onInvestigate} focusRow={traceFocus}/> : page==="records" ? <ResearchHistoryWorkspace key={`${project.project_id}:${threadId}`} projectId={project.project_id} threadId={threadId}
      onUseQuestion={session.problem.trim() ? undefined : question=>{session.setProblem(question);onPage("research");}}/> :
    page==="raw-records" && !hosted ? <><Button minimal icon="arrow-left" onClick={()=>onPage("settings")}>{t("workspace.backSettings")}</Button><DomainRecords key={`${project.project_id}:${threadId}`} projectId={project.project_id} threadId={threadId} operationId={status?.request?.operation_id} onOpenCatalog={namespace=>{setCatalogNamespace(namespace);onPage("capabilities");}}/></> :
    page==="resources" ? <ResourcesPage project={project} hosted={hosted} citation={citation} sourceData={sourceData} sourcesError={sources.error} sourcesDenied={sourcesDenied} onPage={onPage}/> :
    <SettingsPage project={project} threadId={threadId} hosted={hosted} workspaceId={workspaceId} storageScope={storageScope} session={session} capturedEpoch={capturedEpoch} status={status} consent={consent} focusConsent={focusConsent} onConsentFocused={clearConsentFocus} onPage={onPage} onOpenCatalog={()=>{setCatalogNamespace("all");onPage("capabilities");}}/>}
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
  return <div className={`project-nav-row ${active ? "active" : ""}`}>
    <Button minimal alignText="left" className={`project-button ${active ? "active" : ""}`} onClick={onOpen}>
      <Icon icon={active ? "folder-open" : "folder-close"}/><span><strong>{project.name}</strong></span>
    </Button>
    <PopoverNext isOpen={menuOpen} onInteraction={setMenuOpen} placement="right-start" arrow={false} content={<div className="project-inline-menu" role="menu"><Button fill alignText="left" minimal role="menuitem" icon="box" disabled={removing} onClick={()=>{setMenuOpen(false);onRemove();}}>{t("workspace.removeProject")}</Button></div>}>
      <Button small minimal icon="more" aria-label={message("projectMenu", project.name)} aria-expanded={menuOpen} loading={removing}/>
    </PopoverNext>
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

/** What the project holds, from values the shell already reads: sessions from the thread list, sources from the shared sources read. */
function WorkspaceCounts({ sessions, sources }: { sessions: number | null; sources: number | null }) {
  const chips: Array<[string, number | null]> = [[t("workspace.sessionsCount"), sessions], [t("workspace.sourcesCount"), sources]];
  return <div className="workspace-counts" role="group" aria-label={t("workspace.countsLabel")}>{chips.filter(([, n]) => n !== null).map(([label, n]) => <Tag key={label} minimal>{message("tabCount", label, n as number)}</Tag>)}</div>;
}

export function LiveProjectWorkspace() {
  useTranslation();
  const [search, setSearch] = useState("");
  const [openCreate,setOpenCreate] = useState(false);
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
  const selection = useWorkspaceSelection(scopeKey, storageScope, client);
  const { context, page, setPage, restoredFor, contextStoreFailed, contextRestoreIssue, missingSelection, selectionVersion, rowOrigin, traceFocus, epoch, pendingAdmission,
    chooseProject, openProject, chooseThread, cancelOpenLatest, openTraceRow, admit, clearRowOrigin } = selection;
  const project = projects.find(item=>item.project_id===context.projectId);
  const threadsQuery = useQuery({queryKey:["threads",scopeKey,context.projectId],enabled:Boolean(scopeKey && project),queryFn:({signal})=>rpc<{threads:WorkThread[]}>("thread/list",{project_id:context.projectId},crypto.randomUUID(),signal)});
  const threads = useMemo(() => threadsQuery.error ? [] : threadsQuery.data?.value.threads ?? [],
    [threadsQuery.error, threadsQuery.data]);
  const { investigate } = useSelectionReconcile(selection, { projects, project, threads,
    projectsSettled: projectsQuery.isSuccess && !projectsQuery.isFetching, threadsSettled: threadsQuery.isSuccess && !threadsQuery.isFetching });
  const renderedEpoch = epoch.current;
  const visibleProjects = projectsQuery.isFetching ? [] : projects;
  const visibleThreads = threadsQuery.isFetching ? [] : threads;
  const health = useQuery({queryKey:["health"],queryFn:async()=>{const response=await fetch("/healthz",{credentials:"same-origin"});if(!response.ok)throw new Error(`HTTP ${response.status}`);return await response.json() as {status:string;version:string};},retry:false,refetchInterval:30000});
  const shellSources = useProjectSources(project?.project_id ?? "", Boolean(project));
  const archiveProject = useMutation({
    mutationFn: (target: ProjectSummary) => rpc<ProjectSummary>("project/archive", {project_id: target.project_id, expected_revision: target.revision}, crypto.randomUUID()),
    onSuccess: async (_response, target) => {
      await client.invalidateQueries({queryKey:["projects"]});
      await client.invalidateQueries({queryKey:["threads", target.project_id]});
      if (context.projectId === target.project_id) chooseProject("");
    },
    // A stale revision is the usual reason. Read the list again so the next try uses the current one.
    onError: rereadProjects(client),
  });
  const removeProject = (target: ProjectSummary) => {
    if (!window.confirm(message("archiveProject", target.name))) return;
    archiveProject.mutate(target);
  };
  const gate = workspaceGate({ readyQuery, ready, hosted, scopeKey, restoredFor, projectsQuery, projectsCount: projects.length, threadsQuery, hasProject: Boolean(project),
    threadId: context.threadId, threadListed: threads.some(item=>item.thread_id===context.threadId), admissionPending: pendingAdmission.current===context.threadId });
  if (gate || !ready || !scopeKey) return gate;
  const executionReady = executionReadiness(ready, hosted);
  const sourceCount = project && shellSources.data ? shellSources.data.value.artifacts.length : null;
  return <main className="workstation conversation-workstation bp6-dark">
    <aside className="navigation-rail"><div className="brand"><div className="brand-mark"><Icon icon="layout-grid" size={20}/></div><div><strong>THOTH</strong><span>RESEARCH WORKSTATION</span></div><Tag minimal>{hosted ? "REVIEW" : "LOCAL"}</Tag></div>
      <Button icon="plus" intent="primary" className="new-project-button" onClick={()=>{chooseProject("");setOpenCreate(true);}}>{t("workspace.newProject")}</Button>
      <InputGroup small leftIcon="search" aria-label={t("workspace.searchProjects")} placeholder={t("workspace.searchPlaceholder")} value={search} onChange={event=>setSearch(event.target.value)}/>
      <section className="nav-section"><div className="nav-section-heading"><p className="nav-label">PROJECTS</p><Button small minimal icon="refresh" aria-label={t("workspace.refreshProjects")} onClick={()=>void projectsQuery.refetch()}/></div>
        <ProjectActionNotice error={archiveProject.error} fallbackKey="archive.archiveFailed"/>
        {visibleProjects.filter(item=>`${item.name} ${item.project_id}`.toLowerCase().includes(search.toLowerCase())).flatMap(item=>[
          <ProjectNavItem project={item} active={item.project_id===context.projectId} removing={archiveProject.isPending} key={item.project_id} onOpen={()=>openProject(item.project_id)} onRemove={()=>removeProject(item)}/>,
          item.project_id===context.projectId ? <div className="project-sessions" key={`${item.project_id}-sessions`}>
            <Button small minimal icon="plus" className="new-session-button" aria-label={t("workspace.newTask")} onClick={()=>chooseThread("")}>{t("workspace.newSession")}</Button>
            {threadsQuery.isPending&&<p className="empty-copy">{t("workspace.loadingSessions")}</p>}
            {threadsQuery.error&&<Callout compact intent="danger">{threadsQuery.error.message}</Callout>}
            {visibleThreads.map(thread=><Button minimal alignText="left" className={`thread-button ${thread.thread_id===context.threadId?"active":""}`} key={thread.thread_id} onClick={()=>chooseThread(thread.thread_id)}><Icon icon="chat"/><span><strong>{thread.problem||t("workspace.untitledSession")}</strong><small>{thread.execution_state==="RUNNING"?t("workspace.running"):thread.execution_state==="PAUSED"?t("workspace.paused"):t("workspace.reopen")}</small></span></Button>)}
            {!threadsQuery.isPending&&!threadsQuery.error&&threads.length===0&&<p className="empty-copy">{t("workspace.firstQuestion")}</p>}
          </div> : null,
        ])}
        {!projectsQuery.isPending&&!projectsQuery.error&&projects.length===0&&<p className="empty-copy">{t("workspace.noProjects")}</p>}<ArchivedProjects scopeKey={scopeKey} hosted={hosted} storageScope={storageScope} onDeleted={projectId=>{if(context.projectId===projectId)chooseProject("");}}/>
      </section>
      <div className="sidebar-bottom"><Button minimal alignText="left" icon={hosted?"info-sign":"settings"} onClick={()=>setPage("settings")}>{hosted?t("workspace.operations"):t("workspace.settingsTools")}</Button></div>
    </aside>
    <header className="workstation-topbar"><div className="context-path"><Icon icon="projects"/><span>Workspace</span>{import.meta.env.VITE_THOTH_TEST_MODE==="true"&&<Tag intent="warning">{t("workspace.testMode")}</Tag>}<span>/</span><strong>{projectsQuery.isFetching?t("workspace.checkingProject"):project?.name??t("workspace.selectProject")}</strong>{context.threadId&&<><span>/</span><small>{threadsQuery.isFetching?t("workspace.checkingTask"):visibleThreads.find(item=>item.thread_id===context.threadId)?.problem??t("workspace.selectedTask")}</small></>}</div>
      <div className="header-status">{project&&projectCutoffText(project.cutoff_at)&&<Tag minimal icon="time" title={t("workspace.cutoffHint")}>{message("cutoff", projectCutoffText(project.cutoff_at)!)}</Tag>}<Tag minimal intent={health.data?.status==="ok"?"success":health.error?"danger":"none"}>{health.data?.status==="ok"?t("workspace.serverConnected"):health.error?t("workspace.serverFailed"):t("workspace.checkingConnection")}</Tag>
        {!hosted&&!executionReady&&<Button small intent="warning" className="setup-needed-button" icon="warning-sign" endIcon="arrow-right" title={t("workspace.openSetup")} onClick={()=>setPage("settings")}>{t("workspace.setupNeeded")}</Button>}
        {!hosted&&contextStoreFailed&&<Tag minimal intent="warning">{t("workspace.locationUnsaved")}</Tag>}{missingSelection&&<Tag minimal intent="warning">{message("missingSelection", missingSelection)}</Tag>}<Button small minimal icon="refresh" aria-label={t("workspace.refreshContext")} onClick={()=>void client.invalidateQueries()}/></div></header>
    <div className="workspace-navigation"><WorkspaceLocationNotice location={ready.workspace_location} /><Tabs id="workspace-tabs" selectedTabId={page} onChange={value=>setPage(value as Page)}>
      <Tab id="research" title={t("workspace.conversation")}/><Tab id="resources" title={t("workspace.resources")} disabled={!project}/>
      <Tab id="records" title={t("workspace.history")} disabled={!project}/><Tab id="trace" title={t("workspace.trace")} disabled={!project}/><Tab id="settings" title={t("workspace.settings")}/></Tabs>
      {project&&<WorkspaceCounts sessions={threadsQuery.isPending ? null : threads.length} sources={sourceCount}/>}</div>
    {project ? <WorkspaceErrorBoundary resetKey={`${scopeKey}:${project.project_id}:${context.threadId}:${page}`} onRetry={()=>client.invalidateQueries()}><ProjectSession key={`${scopeKey}:${project.project_id}:${selectionVersion}`} project={project} threadId={context.threadId} epoch={epoch} page={page} onPage={setPage} hosted={hosted} workspaceId={workspaceId} browserScopeId={browserScopeId} executionReady={executionReady && !threadsQuery.error} consent={ready.setup?.internet_consent} modelConnected={ready.model_connected}
      readSuspended={readyQuery.isFetching || projectsQuery.isFetching || threadsQuery.isFetching} setupIssue={setupIssue}
      onUserInput={cancelOpenLatest} onAdmitted={admit}
      rowOrigin={rowOrigin} onOriginDone={clearRowOrigin} onInvestigate={investigate} traceFocus={traceFocus} onOpenTrace={openTraceRow}/></WorkspaceErrorBoundary> :
      isTimelineExampleRequested() && page === "research" ? <TimelineExamplePane /> :
      <section className="workspace-content">{missingSelection&&<Callout intent="warning" role="status">{missingSelection==="project"?t("workspace.missingProject"):t("workspace.taskSelectionNeeded")} {t("workspace.draftNotSent")}</Callout>}{contextStoreFailed&&!hosted&&<Callout intent="warning" role="status">{t("workspace.locationFailure")}</Callout>}{!hosted&&!validWorkspaceId(workspaceId)&&<Callout intent="warning" role="status">{t("workspace.identityMissing")}</Callout>}{page==="settings"?<>{hosted?<Callout icon="info-sign" title={t("workspace.chooseProjectNotice")}>{t("workspace.selectForInfo")}</Callout>:<section className="detail-card"><h2>{t("workspace.connectModel")}</h2><ModelCredentialPanel projectId="system:workspace" workspaceId={workspaceId ?? undefined}/><p className="muted">{t("workspace.selectForDefaults")}</p></section>}<LanguageSettingsCard scope={storageScope}/></>:<ProjectLanding openCreate={openCreate} onOpenCreateHandled={()=>setOpenCreate(false)} contextRestoreIssue={contextRestoreIssue} setupIssue={setupIssue} projects={visibleProjects} loading={projectsQuery.isPending||projectsQuery.isFetching} onOpen={openProject} onCreated={projectId=>{if(renderedEpoch===epoch.current)chooseProject(projectId,"resources");}}/>}</section>}
  </main>;
}
