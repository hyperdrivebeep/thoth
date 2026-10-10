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
  return <div className="start-page"><div className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel("RESEARCH WORKSPACE")}</p><h1>질문에서 근거로,<br/>근거에서 다음 행동으로.</h1><p>왼쪽에서 프로젝트를 이어가거나 새로운 연구 맥락을 만드세요.</p></div><Icon icon="projects" size={50} /></div>
    <form className="onboarding-card" onSubmit={event=>{event.preventDefault();submit();}}><h2>새 프로젝트</h2>
      <label>프로젝트 이름<InputGroup required value={name} onChange={event=>setName(event.target.value)} placeholder="연구할 문제 또는 프로젝트 이름" /></label>
      <div className="project-advanced"><Button minimal small icon={advancedOpen?"chevron-up":"chevron-down"} aria-expanded={advancedOpen} onClick={()=>setAdvancedOpen(value=>!value)}>기준시점 · 고급 설정</Button><Collapse isOpen={advancedOpen}><div className="form-row"><label>판단 기준시점<InputGroup required type="datetime-local" value={cutoff} onChange={event=>setCutoff(event.target.value)} /></label>
        <label>도메인 프로필<InputGroup required value={overlay} onChange={event=>setOverlay(event.target.value)} /></label></div></Collapse></div>
      <p className="muted">프로젝트를 만든 뒤 질문을 이어가고 필요한 자료를 연결할 수 있습니다.</p>
      <Button type="submit" icon="plus" intent="primary" disabled={!name.trim() || !cutoff || !overlay.trim()} loading={create.isPending}>프로젝트 만들기</Button>
      {create.error && <Callout intent="danger" role="alert">{create.error.message}</Callout>}</form>
    <div className="start-principles"><div><Icon icon="document-open"/><h3>연결 자료 우선</h3><p>원문 위치와 출처·기준시점을 함께 확인합니다.</p></div><div><Icon icon="comparison"/><h3>반증과 대안</h3><p>미확인·부분 결과를 보존하고 가설과 행동을 비교합니다.</p></div><div><Icon icon="history"/><h3>이어지는 연구 기록</h3><p>결과·기억·revision·복구의 근거를 추적합니다.</p></div></div>
  </div>;
}

type TraceRowRef = { kind: string; id: string };
/** The trace row a question was started from: held here until it is sent, so the next request carries it. */
type RowOrigin = { threadId: string; origin: PendingOrigin };

function ProjectSession({ project, threadId, epoch, page, onPage, onAdmitted, onUserInput, hosted = false, workspaceId = null, executionReady = true, readSuspended = false, setupIssue = null,
  consent, modelConnected, rowOrigin = null, onOriginDone, onInvestigate, traceFocus = null, onOpenTrace }: {
  project: ProjectSummary; threadId: string; epoch: RefObject<number>; page: Page; onPage: (page: Page) => void; onAdmitted: (threadId: string) => void; onUserInput: () => void;
  hosted?: boolean; workspaceId?: string | null; executionReady?: boolean; readSuspended?: boolean; setupIssue?: WorkspaceSetupIssue | null;
  consent?: string; modelConnected?: boolean;
  rowOrigin?: RowOrigin | null; onOriginDone?: () => void; onInvestigate?: (item: RowInvestigation) => void; traceFocus?: TraceRowRef | null; onOpenTrace?: (row: TraceRowRef) => void;
}) {
  const storageScope = useMemo<BrowserScope>(() => hosted ? { mode: "HOSTED" } : { mode: "LOCAL", workspaceId }, [hosted, workspaceId]);
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
    {setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} 권한 있는 저장 연구는 계속 읽을 수 있습니다.</Callout>}
    {page==="research"&&<ResearchComposer session={session} projectId={project.project_id} threadId={threadId} epoch={capturedEpoch}
      onAttach={openResourcePanel} hosted={hosted} executionReady={false} readSuspended/>}</section>;
  return <section ref={contentRef} className={`workspace-content ${page==="research"?"conversation-page":""}`}>
    {setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} 권한 있는 저장 연구는 계속 읽을 수 있지만 새 모델 실행과 동의 변경은 보류합니다.</Callout>}
    {page==="research" ? <div className={`conversation-layout ${panelOpen?"detail-open":""}`}><div className="conversation-center">
      <header className="conversation-heading"><h1>{status?.problem??(threadId?"연구 이어가기":"새로운 질문")}</h1></header>
      {import.meta.env.VITE_THOTH_TEST_MODE==="true"&&<Callout compact intent="warning" className="test-mode-banner">검증 모드 · 고정 응답/통제 자료로 확인하는 화면입니다.</Callout>}
      <div className="conversation-scroll"><ConversationTimeline projectId={project.project_id} threadId={threadId} status={status} sourceUris={(sources.data?.value.artifacts??[]).map(item=>item.source_uri)} onDetail={openDetail} queuedInstruction={session.queuedNotice} onContinue={()=>document.getElementById("live-problem")?.focus()} onRetry={question=>{session.setProblem(question);document.getElementById("live-problem")?.focus();}} onResume={operationId=>session.resume.mutate(operationId)} resuming={session.resume.isPending} onOpenHistory={()=>onPage("records")} onOpenTrace={onOpenTrace}/>
        {session.resume.error&&<Callout intent="danger" role="alert">이어서 조사를 시작하지 못했습니다. {session.resume.error.message}</Callout>}
        {queryError&&<Callout intent="danger" role="alert">{queryError.message}<Button small onClick={()=>{void session.research.refetch();void sources.refetch();}}>다시 읽기</Button></Callout>}
      </div>
      <ResearchComposer session={session} projectId={project.project_id} threadId={threadId} epoch={capturedEpoch} onAttach={openResourcePanel}
        onOpenSettings={target=>{setFocusConsent(target==="consent");onPage("settings");}} hosted={hosted} executionReady={executionReady}
        consentMissing={!hosted && consent==="UNDECIDED"} modelConnected={modelConnected}/>
    </div>{panelOpen&&<ResearchSidePanel tab={sideTab} onTab={setSideTab} detail={detail} onClose={closeDetail} sources={sources.data?.value.artifacts??[]} project={project} thread={{thread_id:threadId,problem:status?.problem??"연구",current_object_ids:status?.current_object_ids??[]}} onOpenRecords={()=>onPage("records")} onManageResources={()=>{closeDetail();onPage("resources");}}/>}</div> :
    page==="capabilities" && !hosted ? <><Button minimal icon="arrow-left" onClick={()=>onPage("settings")}>설정으로 돌아가기</Button><CapabilityWorkbench projectId={project.project_id} threadId={threadId} initialNamespace={catalogNamespace}/></> :
    page==="trace" ? <TracePage projectId={project.project_id} onInvestigate={onInvestigate} focusRow={traceFocus}/> : page==="records" ? <ResearchHistoryWorkspace key={`${project.project_id}:${threadId}`} projectId={project.project_id} threadId={threadId}
      onUseQuestion={session.problem.trim() ? undefined : question=>{session.setProblem(question);onPage("research");}}/> :
    page==="raw-records" && !hosted ? <><Button minimal icon="arrow-left" onClick={()=>onPage("settings")}>설정으로 돌아가기</Button><DomainRecords key={`${project.project_id}:${threadId}`} projectId={project.project_id} threadId={threadId} operationId={status?.request?.operation_id} onOpenCatalog={namespace=>{setCatalogNamespace(namespace);onPage("capabilities");}}/></> :
    page==="resources" ? <><header className="workspace-heading"><div><h1>자료·연결</h1><p>연구에 사용할 파일과 허용할 공개 웹 출처를 관리합니다.</p></div><Button minimal icon="arrow-left" onClick={()=>onPage("research")}>대화로 돌아가기</Button></header><ProjectWebSetup projectId={project.project_id}/><FileConnectionPanel projectId={project.project_id} hosted={hosted}/><h2>연결한 자료</h2>
      {citation.dialog}{citation.message&&<Callout compact>{citation.message}</Callout>}{(sources.data?.value.artifacts??[]).map(artifact=>{ const assessment=(sources.data?.value.source_times??[]).find(item=>item.artifact_id===artifact.artifact_id); const detailsOpen=artifactDetailsId===artifact.artifact_id; return <article className="detail-card" key={artifact.artifact_id}><h3>{isHttpUrl(artifact.source_uri)?<a href={artifact.source_uri} target="_blank" rel="noreferrer">{sourceCardTitle(artifact.source_uri)}</a>:sourceCardTitle(artifact.source_uri)}</h3>{sourceCardHost(artifact.source_uri)?<p>{sourceCardHost(artifact.source_uri)}</p>:null}<p>{artifact.media_type}</p><p>{cutoffLabel(assessment?.cutoff_state ?? artifact.cutoff_state ?? "UNKNOWN_TIME")}</p><Button small onClick={()=>void citation.ask(artifact.artifact_id)}>인용에 사용</Button>{!hosted&&<SourceTimeAdvanced projectId={project.project_id} artifactId={artifact.artifact_id} assessment={assessment} cutoff={sources.data?.value.cutoff_basis}/>} {!hosted&&<><Button small minimal icon={detailsOpen?"chevron-up":"info-sign"} onClick={()=>setArtifactDetailsId(detailsOpen?"":artifact.artifact_id)} aria-expanded={detailsOpen}>출처·추출 상태</Button><Collapse isOpen={detailsOpen}><RecordInspector value={artifact}/></Collapse></>}</article>})}
      {sources.error&&<Callout intent="danger">{sources.error.message}</Callout>}
    </> : <><header className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel(hosted?"REVIEW WORKSPACE":"LOCAL WORKSPACE")}</p><h1>{hosted?"운영 정보":"설정"}</h1><p>{hosted?"심사 환경의 모델과 사용 현황을 확인합니다.":"이 컴퓨터에서 사용할 모델과 프로젝트 도구를 관리합니다."}</p></div></header>
      {hosted ? (
        <section className="detail-card"><h2>심사 모델</h2><p>운영자가 준비한 OpenAI 모델을 사용합니다. 브라우저에 키를 넣지 않습니다.</p></section>
      ) : (
        <section className="detail-card"><h2>모델 기본값</h2><ModelSettings projectId={project.project_id} threadId={threadId||undefined} selection={session.modelSelection} onSelect={session.chooseModel} onSaved={()=>session.clearModel(session.renderedRevision,capturedEpoch)}/><AutoRetrySwitch projectId={project.project_id}/><HypothesisContractSwitch projectId={project.project_id}/><ModelCredentialPanel projectId={project.project_id} workspaceId={workspaceId ?? undefined}/></section>
      )}
      {!hosted && consent !== undefined && <WorkspaceInternetConsent consent={consent} focus={focusConsent} onFocused={clearConsentFocus}/>}
      <section className="detail-card"><h2>사용량</h2><p>보고된 사용 토큰: {status?.usage?.total_tokens?.toLocaleString()??"아직 없음"}{status?.usage?.state==="PARTIAL"?" (부분 관측)":""}</p><p>진행 중인 호출의 사용량은 아직 포함되지 않을 수 있습니다.</p>{!hosted&&<><Button small minimal icon={projectDetailsOpen?"chevron-up":"settings"} onClick={()=>setProjectDetailsOpen(value=>!value)} aria-expanded={projectDetailsOpen}>현재 프로젝트 설정</Button><Collapse isOpen={projectDetailsOpen}><RecordInspector value={project}/></Collapse></>}</section>
      <section className="detail-card"><h2>{hosted?"기록과 자료":"기록과 로컬 도구"}</h2><Button minimal icon="folder-open" onClick={()=>onPage("resources")}>자료·연결 관리</Button><Button minimal icon="history" onClick={()=>onPage("records")}>연구 이력</Button>{!hosted&&<><Button minimal icon={developerOpen?"chevron-up":"chevron-down"} onClick={()=>setDeveloperOpen(value=>!value)} aria-expanded={developerOpen}>로컬 개발자 도구</Button><Collapse isOpen={developerOpen}><div className="local-developer-tools"><Button minimal icon="database" onClick={()=>onPage("raw-records")}>상세 기록 열기</Button><Button minimal icon="code" onClick={()=>{setCatalogNamespace("all");onPage("capabilities");}}>상세 API 도구 열기</Button><RecordInspector value={{request:status?.request,attempt:status?.attempt,budget:status?.budget,usage:status?.usage,cleanup:status?.cleanup}}/></div></Collapse></>}</section>
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
    <Button small minimal icon="more" aria-label={`${project.name} 프로젝트 메뉴`} aria-expanded={menuOpen} loading={removing} onClick={()=>setMenuOpen(value=>!value)}/>
    {menuOpen&&<div className="project-inline-menu" role="menu"><Button fill alignText="left" minimal role="menuitem" icon="trash" intent="danger" disabled={removing} onClick={()=>{setMenuOpen(false);onRemove();}}>프로젝트 제거</Button></div>}
  </div>;
}

export function ProjectLanding({ projects, loading = false, contextRestoreIssue = null, setupIssue = null, onOpen, onCreated }: {
  projects: ProjectSummary[];
  loading?: boolean;
  contextRestoreIssue?: "CORRUPT" | "UNAVAILABLE" | null;
  setupIssue?: WorkspaceSetupIssue | null;
  onOpen: (projectId: string) => void;
  onCreated: (projectId: string) => void;
}) {
  const [creating,setCreating] = useState(false);
  useEffect(()=>{if(!loading&&projects.length===0)setCreating(true);},[loading,projects.length]);
  const restoreNotice = contextRestoreIssue && <Callout compact intent="warning" role="alert">{contextRestoreIssue === "CORRUPT"
    ? "마지막 작업 위치 기록을 읽지 못했습니다. 원본을 자동 삭제하지 않았습니다."
    : "이 브라우저의 마지막 작업 위치를 읽지 못했습니다. 저장소 접근과 작업 공간 식별자를 확인하세요."}
    {" "}다른 주소·포트·브라우저 프로필 또는 작업 공간의 선택과 초안은 자동 병합되지 않습니다. 프로젝트를 직접 선택하면 현재 위치를 새로 저장합니다.</Callout>;
  const setupNotice = setupIssue && <Callout compact intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} 기존 프로젝트가 있으면 권한 있는 저장 연구를 열 수 있습니다.</Callout>;
  if (creating) return <>{setupNotice}{restoreNotice}<Button minimal icon="arrow-left" disabled={projects.length===0} onClick={()=>setCreating(false)}>프로젝트 선택으로 돌아가기</Button><ProjectForm onCreated={onCreated}/></>;
  return <div className="start-page project-home">{setupNotice}{restoreNotice}<div className="workspace-heading"><div><p className="eyebrow">{eyebrowLabel("RESEARCH WORKSPACE")}</p><h1>연구를 이어가거나<br/>새 문제를 시작하세요.</h1><p>기존 프로젝트를 열어 작업을 이어가거나, 새로운 연구 맥락을 만들 수 있습니다.</p></div><Icon icon="projects" size={50}/></div>
    <section className="project-home-list" aria-label="이어갈 프로젝트"><div className="section-title-row"><h2>프로젝트</h2><Button icon="plus" intent="primary" onClick={()=>setCreating(true)}>새 프로젝트</Button></div>
      {loading&&<p className="empty-copy" role="status">프로젝트를 읽는 중…</p>}
      {projects.map(project=><Button fill large alignText="left" className="project-home-item" key={project.project_id} icon="folder-open" onClick={()=>onOpen(project.project_id)}><span><strong>{project.name}</strong><small>프로젝트 열기</small></span></Button>)}
    </section>
    <p className="muted">읽을 수 없는 기록은 새 답변으로 대체하지 않습니다.</p>
  </div>;
}

export function LiveProjectWorkspace() {
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
  const workspaceId = !hosted && validWorkspaceId(ready?.workspace_id) ? ready.workspace_id : null;
  const storageScope = useMemo<BrowserScope>(() => hosted ? { mode: "HOSTED" } : { mode: "LOCAL", workspaceId }, [hosted, workspaceId]);
  const scopeKey = ready ? hosted ? "HOSTED" : `LOCAL:${workspaceId ?? "UNBOUND"}` : null;
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
    writeDraft(project.project_id, target, fillQuestion(readDraft(project.project_id, target, storageScope), item.question), storageScope);
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
    if (!window.confirm(`프로젝트 "${target.name}"을 목록에서 제거할까요? 데이터는 보관되고 기본 목록에서 숨겨집니다.`)) return;
    archiveProject.mutate(target);
  };
  if (readyQuery.isPending || (readyQuery.isSuccess && !ready)) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">준비 확인 중…</p></main>;
  }
  if (readyQuery.error) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="danger" title="서버 준비 확인 실패">서버 준비 상태를 확인하지 못했습니다. 연결 상태를 확인하고 다시 시도하세요.<Button small onClick={()=>void readyQuery.refetch()}>다시 확인</Button></Callout></section></main>;
  }
  if (!ready || !scopeKey || restoredFor !== scopeKey) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">이 워크스페이스의 저장된 작업을 확인하는 중…</p></main>;
  }
  if (hosted && !ready.ready) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  if (projectsQuery.isPending && !projectsQuery.data) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">읽을 수 있는 프로젝트를 확인하는 중…</p></main>;
  }
  if (projectsQuery.error) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="warning" role="alert">프로젝트 접근 범위를 확인하지 못했습니다. 저장된 작업을 새 프로젝트로 대체하지 않습니다.<Button small onClick={()=>void projectsQuery.refetch()}>다시 확인</Button></Callout></section></main>;
  }
  const workspaceReadable = hosted || ready.workspace_readable !== false;
  const setupComplete = ready.setup_complete ?? (ready.setup?.internet_consent !== undefined && ready.setup.internet_consent !== "UNDECIDED");
  const executionReady = hosted ? ready.ready : (ready.execution_ready ?? ready.ready);
  if (!workspaceReadable) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="warning" role="alert">이 워크스페이스의 저장된 연구를 지금 읽을 수 없습니다. 접근 상태를 확인하세요.<Button small onClick={()=>void readyQuery.refetch()}>다시 확인</Button></Callout></section></main>;
  }
  if (!hosted && ready.workspace_readable === undefined && !ready.ready) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  if (!hosted && !setupComplete && projects.length === 0) {
    return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  }
  if (context.threadId && project && threadsQuery.isPending && !threadsQuery.data) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">선택한 작업의 접근 범위를 확인하는 중…</p></main>;
  }
  if (context.threadId && project && threadsQuery.error) {
    return <main className="first-run-shell bp6-dark"><section className="first-run-card"><Callout intent="warning" role="alert">선택한 작업을 확인하지 못했습니다. 초안은 유지합니다.<Button small onClick={()=>void threadsQuery.refetch()}>다시 확인</Button></Callout></section></main>;
  }
  if (context.threadId && project && !threads.some(item=>item.thread_id===context.threadId) && pendingAdmission.current!==context.threadId) {
    return <main className="first-run-shell bp6-dark"><p className="empty-copy" role="status">저장된 작업 선택을 다시 확인하는 중…</p></main>;
  }
  return <main className="workstation conversation-workstation bp6-dark">
    <aside className="navigation-rail"><div className="brand"><div className="brand-mark"><Icon icon="layout-grid" size={20}/></div><div><strong>THOTH</strong><span>RESEARCH WORKSTATION</span></div><Tag minimal>{hosted ? "REVIEW" : "LOCAL"}</Tag></div>
      <Button icon="plus" intent="primary" className="new-project-button" onClick={()=>chooseProject("")}>새 프로젝트</Button>
      <InputGroup small leftIcon="search" aria-label="프로젝트 검색" placeholder="프로젝트 찾기…" value={search} onChange={event=>setSearch(event.target.value)}/>
      <section className="nav-section"><div className="nav-section-heading"><p className="nav-label">PROJECTS</p><Button small minimal icon="refresh" aria-label="프로젝트 목록 새로고침" onClick={()=>void projectsQuery.refetch()}/></div>
        {visibleProjects.filter(item=>`${item.name} ${item.project_id}`.toLowerCase().includes(search.toLowerCase())).flatMap(item=>[
          <ProjectNavItem project={item} active={item.project_id===context.projectId} removing={archiveProject.isPending} key={item.project_id} onOpen={()=>openProject(item.project_id)} onRemove={()=>removeProject(item)}/>,
          item.project_id===context.projectId ? <div className="project-sessions" key={`${item.project_id}-sessions`}>
            <Button small minimal icon="plus" className="new-session-button" aria-label="새 작업" onClick={()=>chooseThread("")}>새 세션</Button>
            {archiveProject.error&&<Callout compact intent="danger">{archiveProject.error.message}</Callout>}
            {threadsQuery.isPending&&<p className="empty-copy">세션을 읽는 중…</p>}
            {threadsQuery.error&&<Callout compact intent="danger">{threadsQuery.error.message}</Callout>}
            {visibleThreads.map(thread=><Button minimal alignText="left" className={`thread-button ${thread.thread_id===context.threadId?"active":""}`} key={thread.thread_id} onClick={()=>chooseThread(thread.thread_id)}><Icon icon="chat"/><span><strong>{thread.problem||"제목 없는 세션"}</strong><small>{thread.execution_state==="RUNNING"?"진행 중":thread.execution_state==="PAUSED"?"일시정지":"이어서 보기"}</small></span></Button>)}
            {!threadsQuery.isPending&&!threadsQuery.error&&threads.length===0&&<p className="empty-copy">첫 질문을 보내면 이 프로젝트 아래 세션이 생깁니다.</p>}
          </div> : null,
        ])}
        {!projectsQuery.isPending&&!projectsQuery.error&&projects.length===0&&<p className="empty-copy">아직 프로젝트가 없습니다.</p>}
      </section>
      <div className="sidebar-bottom"><Button minimal alignText="left" icon={hosted?"info-sign":"settings"} onClick={()=>setPage("settings")}>{hosted?"운영 정보":"설정 · 로컬 도구"}</Button></div>
    </aside>
    <header className="workstation-topbar"><div className="context-path"><Icon icon="projects"/><span>Workspace</span>{import.meta.env.VITE_THOTH_TEST_MODE==="true"&&<Tag intent="warning">검증 모드</Tag>}<span>/</span><strong>{projectsQuery.isFetching?"프로젝트 확인 중":project?.name??"프로젝트 선택"}</strong>{context.threadId&&<><span>/</span><small>{threadsQuery.isFetching?"작업 확인 중":visibleThreads.find(item=>item.thread_id===context.threadId)?.problem??"선택한 작업"}</small></>}</div>
      <div className="header-status">{project&&projectCutoffText(project.cutoff_at)&&<Tag minimal icon="time" title="이 프로젝트가 판단에 쓰는 자료의 기준시점">기준시점 {projectCutoffText(project.cutoff_at)}</Tag>}<Tag minimal intent={health.data?.status==="ok"?"success":health.error?"danger":"none"}>{health.data?.status==="ok"?"서버 연결됨":health.error?"서버 연결 실패":"연결 확인 중"}</Tag>{!hosted&&!executionReady&&<Tag minimal intent="warning">새 연구 준비 필요</Tag>}{!hosted&&contextStoreFailed&&<Tag minimal intent="warning">작업 위치 저장 실패</Tag>}{missingSelection&&<Tag minimal intent="warning">저장된 {missingSelection==="project"?"프로젝트":"작업"} 선택 확인 필요</Tag>}<Button small minimal icon="refresh" aria-label="현재 맥락 새로고침" onClick={()=>void client.invalidateQueries()}/></div></header>
    <div className="workspace-navigation"><Tabs id="workspace-tabs" selectedTabId={page} onChange={value=>setPage(value as Page)}><Tab id="research" title="대화"/><Tab id="resources" title="자료·연결" disabled={!project}/><Tab id="records" title="연구 이력" disabled={!project}/><Tab id="trace" title="추적표" disabled={!project}/><Tab id="settings" title="설정"/></Tabs>
      {project&&<div className="context-policy"><HTMLSelect aria-label="현재 작업 선택" disabled={threadsQuery.isFetching} value={context.threadId} onChange={event=>chooseThread(event.target.value)} options={[{value:"",label:"새 작업"},...visibleThreads.map(item=>({value:item.thread_id,label:(item.problem||"제목 없는 작업").slice(0,40)}))]}/></div>}</div>
    {project ? <WorkspaceErrorBoundary resetKey={`${scopeKey}:${project.project_id}:${context.threadId}:${page}`} onRetry={()=>client.invalidateQueries()}><ProjectSession key={`${scopeKey}:${project.project_id}:${selectionVersion}`} project={project} threadId={context.threadId} epoch={epoch} page={page} onPage={setPage} hosted={hosted} workspaceId={workspaceId} executionReady={executionReady && !threadsQuery.error} consent={ready.setup?.internet_consent} modelConnected={ready.model_connected}
      readSuspended={readyQuery.isFetching || projectsQuery.isFetching || threadsQuery.isFetching} setupIssue={setupIssue}
      onUserInput={cancelOpenLatest} onAdmitted={threadId=>{pendingAdmission.current=threadId;setContext(current=>({...current,threadId}));}}
      rowOrigin={rowOrigin} onOriginDone={()=>setRowOrigin(null)} onInvestigate={investigate} traceFocus={traceFocus} onOpenTrace={openTraceRow}/></WorkspaceErrorBoundary> :
      isTimelineExampleRequested() && page === "research" ? <TimelineExamplePane /> :
      <section className="workspace-content">{missingSelection&&<Callout intent="warning" role="status">{missingSelection==="project"?"저장된 프로젝트를 현재 목록에서 확인하지 못해 선택을 비웠습니다.":"저장된 작업 선택 확인 필요"} 저장된 초안은 자동 제출하지 않습니다.</Callout>}{contextStoreFailed&&!hosted&&<Callout intent="warning" role="status">이 브라우저에 마지막 작업 위치를 저장하지 못했습니다. 재시작 후 자동 복원이 제한될 수 있습니다.</Callout>}{!hosted&&!validWorkspaceId(workspaceId)&&<Callout intent="warning" role="status">워크스페이스 식별자를 확인하지 못해 브라우저 재시작용 초안 저장을 사용할 수 없습니다.</Callout>}{page==="settings"?<>{hosted?<Callout icon="info-sign" title="프로젝트를 선택하세요">운영 정보와 사용 현황은 프로젝트를 선택한 뒤 확인할 수 있습니다.</Callout>:<section className="detail-card"><h2>모델 연결</h2><ModelCredentialPanel projectId="system:workspace" workspaceId={workspaceId ?? undefined}/><p className="muted">프로젝트별 기본 모델은 프로젝트를 선택한 뒤 설정합니다.</p></section>}</>:<ProjectLanding contextRestoreIssue={contextRestoreIssue} setupIssue={setupIssue} projects={visibleProjects} loading={projectsQuery.isPending||projectsQuery.isFetching} onOpen={openProject} onCreated={projectId=>{if(renderedEpoch===epoch.current)chooseProject(projectId,"resources");}}/>}</section>}
  </main>;
}

