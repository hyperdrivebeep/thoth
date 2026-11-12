import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { sentenceGap } from "../i18n/sentences";
import { Button, Callout, InputGroup, Tab, Tabs, Tag } from "@blueprintjs/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { flushSync } from "react-dom";
import { rpc } from "../api/rpcClient";
import { connectedArtifacts, detachedArtifactIds } from "../api/sourceBindings";
import { DetachedSourcesContext } from "./detachedSources";
import { AskedQuestionContext, askedQuestion } from "./askedQuestion";
import { threadName } from "../api/threadName";
import { ProjectNavItem } from "./ProjectNavItem";
import { ThreadNavItem } from "./ThreadNavItem";
import { useProjectManagement } from "./useProjectManagement";
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
import {
  workspaceSetupIssue,
  workspaceSetupIssueText,
  type WorkspaceSetupIssue,
} from "./workspaceSetupIssue";
import { LanguageSettingsCard } from "./LanguageSettingsCard";
import { useResearchSession } from "./useResearchSession";
import { useReadAccessProtection } from "./useReadAccessProtection";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";
import { ConversationTimeline } from "./ConversationTimeline";
import { ArchivedProjects } from "./ArchivedProjects";
import { WorkspaceContent } from "./WorkspaceContent";
import { WORKSPACE_TABS_ID, WorkspacePageContext } from "./workspaceTabs";
import { Brand } from "./Brand";
import { ProjectForm } from "./ProjectForm";
import { useNavigationDrawer } from "./useNavigationDrawer";
import { WorkspaceTopbar } from "./WorkspaceTopbar";
import { ResearchComposer } from "./ResearchComposer";
import { ResearchSidePanel, type SidePanelTab } from "./ResearchSidePanel";
import { TracePage } from "./TracePage";
import type { ResearchDetail } from "./ResearchResultCard";
import { ProjectReadinessCard, type ReadinessFacts } from "./ProjectReadinessCard";
import { ComputerSettingsPage, ResourcesPage, SettingsPages } from "./ProjectSessionPages";
import { useProjectSources } from "./useProjectSources";
import { executionReadiness, workspaceGate } from "./WorkspaceGate";
import {
  settingsPageFor,
  useSelectionReconcile,
  useWorkspaceSelection,
  type Page,
  type RowOrigin,
  type TraceRowRef,
} from "./workspaceSelection";
import {
  isTimelineExampleRequested,
  TIMELINE_EXAMPLE_SOURCES,
  timelineExampleStatus,
} from "../api/timelineExample";

function TimelineExamplePane() {
  return (
    <WorkspaceContent className="conversation-page">
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
    </WorkspaceContent>
  );
}

function ProjectSession({
  project,
  threadId,
  epoch,
  page,
  onPage,
  onAdmitted,
  onUserInput,
  hosted = false,
  workspaceId = null,
  browserScopeId = null,
  executionReady = true,
  readSuspended = false,
  setupIssue = null,
  consent,
  modelConnected,
  rowOrigin = null,
  onOriginDone,
  onInvestigate,
  traceFocus = null,
  onOpenTrace,
}: {
  project: ProjectSummary;
  threadId: string;
  epoch: RefObject<number>;
  page: Page;
  onPage: (page: Page) => void;
  onAdmitted: (threadId: string) => void;
  onUserInput: () => void;
  hosted?: boolean;
  workspaceId?: string | null;
  browserScopeId?: string | null;
  executionReady?: boolean;
  readSuspended?: boolean;
  setupIssue?: WorkspaceSetupIssue | null;
  consent?: string;
  modelConnected?: boolean;
  rowOrigin?: RowOrigin | null;
  onOriginDone?: () => void;
  onInvestigate?: (item: RowInvestigation) => void;
  traceFocus?: TraceRowRef | null;
  onOpenTrace?: (row: TraceRowRef) => void;
}) {
  const storageScope = useMemo<BrowserScope>(
    () => (hosted ? { mode: "HOSTED", browserScopeId } : { mode: "LOCAL", workspaceId }),
    [hosted, workspaceId, browserScopeId],
  );
  const origin = rowOrigin && rowOrigin.threadId === threadId ? rowOrigin.origin : null;
  const session = useResearchSession(
    project.project_id,
    threadId,
    epoch,
    onAdmitted,
    storageScope,
    executionReady,
    origin,
    onOriginDone,
  );
  useEffect(() => {
    if (origin && page === "research") document.getElementById("live-problem")?.focus();
  }, [origin, page]);
  const contentRef = useRef<HTMLElement | null>(null);
  // Once the user has typed or submitted a question here, the work list arriving later must not move them elsewhere.
  const started = session.problem.trim() !== "" || session.submit.isPending;
  useEffect(() => {
    if (started) onUserInput();
  }, [started, onUserInput]);
  useEffect(() => {
    if (page === "records" && contentRef.current) contentRef.current.scrollTop = 0;
  }, [page]);
  const [focusConsent, setFocusConsent] = useState(false);
  const clearConsentFocus = useCallback(() => setFocusConsent(false), []);
  const [catalogNamespace, setCatalogNamespace] = useState("all");
  const [detail, setDetail] = useState<ResearchDetail | null>(null);
  const detailFromResearch = useRef(false);
  useEffect(() => {
    if (session.researchReadDenied && detailFromResearch.current) {
      detailFromResearch.current = false;
      setDetail(null);
      setPanelOpen(false);
    }
  }, [session.researchReadDenied]);
  const [sideTab, setSideTab] = useState<SidePanelTab>("files");
  const [panelOpen, setPanelOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const openDetail = (value: ResearchDetail) => {
    detailFromResearch.current =
      value.result === session.research.data?.value.current_result?.result ||
      value.result === session.research.data?.value.previous_result?.result;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDetail(value);
    setSideTab(
      value.kind === "actions"
        ? "actions"
        : value.kind === "hypotheses"
          ? "hypotheses"
          : value.kind === "history" || value.kind === "memory"
            ? "history"
            : value.kind === "evidence"
              ? "evidence"
              : sideTab,
    );
    setPanelOpen(true);
  };
  const closeDetail = () => {
    // The page is drawn again first: in a narrow window the panel covers the conversation and hides what opened it.
    flushSync(() => {
      setDetail(null);
      setPanelOpen(false);
    });
    opener.current?.focus();
  };
  const openResourcePanel = () => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDetail(null);
    setSideTab("files");
    setPanelOpen(true);
  };
  const capturedEpoch = epoch.current;
  const citation = useCitationGate(project.project_id, session.isCurrentScope);
  const sources = useProjectSources(project.project_id);
  const sourcesDenied = useReadAccessProtection(["sources", project.project_id], sources.error);
  const sourceData = sourcesDenied ? undefined : sources.data;
  const status = session.research.data?.value;
  const detachedSources = useMemo(() => detachedArtifactIds(sourceData?.value), [sourceData]);
  const queryError = session.research.error ?? sources.error;
  const openSettings = (target?: "consent" | "project") => {
    setFocusConsent(target === "consent");
    onPage(settingsPageFor(hosted, target));
  };
  // A thread with nothing sent yet: the readiness card carries the setup notice, so the composer does not repeat it.
  const freshConversation = !threadId && !status?.request && status?.operation_state !== "RUNNING";
  const readiness: ReadinessFacts = {
    sourceCount: sourceData ? connectedArtifacts(sourceData.value).length : null,
    modelConnected,
    consent,
    cutoffAt: project.cutoff_at,
    executionReady,
    hosted,
  };
  if (readSuspended)
    return (
      <WorkspaceContent className="conversation-page">
        {setupIssue && (
          <Callout compact intent="warning" role="alert">
            {workspaceSetupIssueText(setupIssue)}
            {sentenceGap()}
            {t("workspace.authorizedRead")}
          </Callout>
        )}
        {page === "research" && (
          <ResearchComposer
            session={session}
            projectId={project.project_id}
            threadId={threadId}
            epoch={capturedEpoch}
            onAttach={openResourcePanel}
            hosted={hosted}
            executionReady={false}
            readSuspended
          />
        )}
      </WorkspaceContent>
    );
  return (
    <WorkspaceContent
      ref={contentRef}
      className={page === "research" ? "conversation-page" : undefined}
    >
      {setupIssue && (
        <Callout compact intent="warning" role="alert">
          {workspaceSetupIssueText(setupIssue)}
          {sentenceGap()}
          {t("workspace.readOnlyHold")}
        </Callout>
      )}
      {page === "research" ? (
        <div className={`conversation-layout ${panelOpen ? "detail-open" : ""}`}>
          <div className="conversation-center">
            <header className="conversation-heading">
              <h1>
                {threadName(status) ||
                  (threadId ? t("workspace.continue") : t("workspace.newQuestion"))}
              </h1>
            </header>
            {import.meta.env.VITE_THOTH_TEST_MODE === "true" && (
              <Callout compact intent="warning" className="test-mode-banner">
                {t("workspace.testModeNotice")}
              </Callout>
            )}
            <div className="conversation-scroll">
              <ConversationTimeline
                projectId={project.project_id}
                threadId={threadId}
                status={status}
                sourceUris={(sourceData?.value.artifacts ?? []).map((item) => item.source_uri)}
                onDetail={openDetail}
                queuedInstruction={session.queuedNotice}
                onContinue={() => document.getElementById("live-problem")?.focus()}
                onRetry={(question) => {
                  session.setProblem(question);
                  document.getElementById("live-problem")?.focus();
                }}
                onResume={(operationId) => session.resume.mutate(operationId)}
                resuming={session.resume.isPending}
                onOpenHistory={() => onPage("records")}
                onOpenSettings={() => openSettings("project")}
                onOpenTrace={onOpenTrace}
                emptyNotice={
                  <ProjectReadinessCard
                    facts={readiness}
                    onOpenSettings={openSettings}
                    onManageSources={() => onPage("resources")}
                  />
                }
              />
              {session.resume.error && (
                <Callout intent="danger" role="alert">
                  {t("workspace.resumeFailed")}
                  {sentenceGap()}
                  {session.resume.error.message}
                </Callout>
              )}
              {(queryError || sourcesDenied || session.researchReadDenied) && (
                <Callout intent="danger" role="alert">
                  {queryError?.message ?? t("workspace.accessFailed")}
                  <Button
                    small
                    onClick={() => {
                      void session.research.refetch();
                      void sources.refetch();
                    }}
                  >
                    {t("workspace.reload")}
                  </Button>
                </Callout>
              )}
            </div>
            <ResearchComposer
              session={session}
              projectId={project.project_id}
              threadId={threadId}
              epoch={capturedEpoch}
              onAttach={openResourcePanel}
              onOpenSettings={openSettings}
              hosted={hosted}
              executionReady={executionReady}
              setupNoticeShown={freshConversation}
              consentMissing={!hosted && consent === "UNDECIDED"}
              modelConnected={modelConnected}
            />
          </div>
          {panelOpen && !(session.researchReadDenied && detailFromResearch.current) && (
            <DetachedSourcesContext.Provider value={detachedSources}>
              <AskedQuestionContext.Provider value={askedQuestion(status)}>
                <ResearchSidePanel
                  tab={sideTab}
                  onTab={setSideTab}
                  detail={detail}
                  onClose={closeDetail}
                  sources={connectedArtifacts(sourceData?.value)}
                  project={project}
                  thread={{
                    thread_id: threadId,
                    problem: status?.problem ?? t("workspace.research"),
                    current_object_ids: status?.current_object_ids ?? [],
                  }}
                  onOpenRecords={() => onPage("records")}
                  onManageResources={() => {
                    closeDetail();
                    onPage("resources");
                  }}
                />
              </AskedQuestionContext.Provider>
            </DetachedSourcesContext.Provider>
          )}
        </div>
      ) : page === "capabilities" && !hosted ? (
        <>
          <Button minimal icon="arrow-left" onClick={() => onPage("settings")}>
            {t("workspace.backSettings")}
          </Button>
          <CapabilityWorkbench
            projectId={project.project_id}
            threadId={threadId}
            initialNamespace={catalogNamespace}
          />
        </>
      ) : page === "trace" ? (
        <TracePage
          projectId={project.project_id}
          responseLanguage={session.responseLanguage}
          onInvestigate={onInvestigate}
          focusRow={traceFocus}
        />
      ) : page === "records" ? (
        <ResearchHistoryWorkspace
          key={`${project.project_id}:${threadId}`}
          projectId={project.project_id}
          threadId={threadId}
          onUseQuestion={
            session.problem.trim()
              ? undefined
              : (question) => {
                  session.setProblem(question);
                  onPage("research");
                }
          }
        />
      ) : page === "raw-records" && !hosted ? (
        <>
          <Button minimal icon="arrow-left" onClick={() => onPage("settings")}>
            {t("workspace.backSettings")}
          </Button>
          <DomainRecords
            key={`${project.project_id}:${threadId}`}
            projectId={project.project_id}
            threadId={threadId}
            operationId={status?.request?.operation_id}
            onOpenCatalog={(namespace) => {
              setCatalogNamespace(namespace);
              onPage("capabilities");
            }}
          />
        </>
      ) : page === "resources" ? (
        <ResourcesPage
          project={project}
          hosted={hosted}
          citation={citation}
          sourceData={sourceData}
          sourcesError={sources.error}
          sourcesDenied={sourcesDenied}
          onPage={onPage}
          onOpenConsent={() => openSettings("consent")}
        />
      ) : (
        <SettingsPages
          page={page}
          modelConnected={modelConnected}
          project={project}
          threadId={threadId}
          hosted={hosted}
          workspaceId={workspaceId}
          storageScope={storageScope}
          session={session}
          capturedEpoch={capturedEpoch}
          status={status}
          consent={consent}
          focusConsent={focusConsent}
          onConsentFocused={clearConsentFocus}
          onPage={onPage}
          onOpenCatalog={() => {
            setCatalogNamespace("all");
            onPage("capabilities");
          }}
        />
      )}
    </WorkspaceContent>
  );
}

export function ProjectLanding({
  projects,
  loading = false,
  contextRestoreIssue = null,
  setupIssue = null,
  onOpen,
  onCreated,
  openCreate = false,
  onOpenCreateHandled,
}: {
  projects: ProjectSummary[];
  loading?: boolean;
  contextRestoreIssue?: "CORRUPT" | "UNAVAILABLE" | null;
  setupIssue?: WorkspaceSetupIssue | null;
  onOpen: (projectId: string) => void;
  onCreated: (projectId: string) => void;
  openCreate?: boolean;
  onOpenCreateHandled?: () => void;
}) {
  useTranslation();
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    if (!loading && projects.length === 0) setCreating(true);
  }, [loading, projects.length]);
  useEffect(() => {
    if (openCreate) {
      setCreating(true);
      onOpenCreateHandled?.();
    }
  }, [openCreate, onOpenCreateHandled]);
  const restoreNotice = contextRestoreIssue && (
    <Callout compact intent="warning" role="alert">
      {contextRestoreIssue === "CORRUPT"
        ? t("workspace.contextCorrupt")
        : t("workspace.contextUnavailable")}
      {sentenceGap()}
      {t("workspace.restoreScope")}
    </Callout>
  );
  const setupNotice = setupIssue && (
    <Callout compact intent="warning" role="alert">
      {workspaceSetupIssueText(setupIssue)}
      {sentenceGap()}
      {t("workspace.existingRead")}
    </Callout>
  );
  if (creating)
    return (
      <>
        {setupNotice}
        {restoreNotice}
        <Button
          minimal
          icon="arrow-left"
          disabled={projects.length === 0}
          onClick={() => setCreating(false)}
        >
          {t("workspace.backProjects")}
        </Button>
        <ProjectForm onCreated={onCreated} />
      </>
    );
  return (
    <div className="start-page project-home">
      {setupNotice}
      {restoreNotice}
      <section className="project-home-list" aria-labelledby="project-home-title">
        <div className="section-title-row">
          <h1 id="project-home-title">{t("workspace.projects")}</h1>
          <Button icon="plus" intent="primary" onClick={() => setCreating(true)}>
            {t("workspace.newProject")}
          </Button>
        </div>
        {loading && (
          <p className="empty-copy" role="status">
            {t("workspace.loadingProjects")}
          </p>
        )}
        {projects.map((project) => (
          <Button
            fill
            large
            alignText="left"
            className="project-home-item"
            key={project.project_id}
            icon="folder-open"
            onClick={() => onOpen(project.project_id)}
          >
            <span>
              <strong>{project.name}</strong>
              <small>{t("workspace.openProject")}</small>
            </span>
          </Button>
        ))}
      </section>
      <p className="muted">{t("workspace.noReplacement")}</p>
    </div>
  );
}

function currentThreadLabel(checking: boolean, threads: WorkThread[], threadId: string): string {
  if (checking) return t("workspace.checkingTask");
  return (
    threadName(threads.find((item) => item.thread_id === threadId)) || t("workspace.selectedTask")
  );
}

/** The page when no project is open: the project list, or this computer's settings. */
function NoProjectPage({
  page,
  hosted,
  workspaceId,
  storageScope,
  consent,
  missingSelection,
  contextStoreFailed,
  landing,
}: {
  page: Page;
  hosted: boolean;
  workspaceId: string | null;
  storageScope: BrowserScope;
  consent?: string;
  missingSelection: "project" | "thread" | null;
  contextStoreFailed: boolean;
  landing: ReactNode;
}) {
  const body = () => {
    if (page === "computer" && !hosted)
      return (
        <ComputerSettingsPage
          credentialProjectId="system:workspace"
          workspaceId={workspaceId}
          storageScope={storageScope}
          consent={consent}
        />
      );
    if (page === "settings" && hosted) {
      return (
        <>
          <Callout icon="info-sign" title={t("workspace.chooseProjectNotice")}>
            {t("workspace.selectForInfo")}
          </Callout>
          <LanguageSettingsCard scope={storageScope} />
        </>
      );
    }
    return landing;
  };
  return (
    <WorkspaceContent>
      {missingSelection && (
        <Callout intent="warning" role="status">
          {missingSelection === "project"
            ? t("workspace.missingProject")
            : t("workspace.taskSelectionNeeded")}
          {sentenceGap()}
          {t("workspace.draftNotSent")}
        </Callout>
      )}
      {contextStoreFailed && !hosted && (
        <Callout intent="warning" role="status">
          {t("workspace.locationFailure")}
        </Callout>
      )}
      {!hosted && !validWorkspaceId(workspaceId) && (
        <Callout intent="warning" role="status">
          {t("workspace.identityMissing")}
        </Callout>
      )}
      {body()}
    </WorkspaceContent>
  );
}

/** What the project holds, from values the shell already reads: sessions from the thread list, sources from the shared sources read. */
function WorkspaceCounts({
  sessions,
  sources,
}: {
  sessions: number | null;
  sources: number | null;
}) {
  const chips: Array<[string, number | null]> = [
    [t("workspace.sessionsCount"), sessions],
    [t("workspace.sourcesCount"), sources],
  ];
  return (
    <div className="workspace-counts" role="group" aria-label={t("workspace.countsLabel")}>
      {chips
        .filter(([, n]) => n !== null)
        .map(([label, n]) => (
          <Tag key={label} minimal>
            {message("tabCount", label, n as number)}
          </Tag>
        ))}
    </div>
  );
}

export function LiveProjectWorkspace() {
  useTranslation();
  const [search, setSearch] = useState("");
  const [openCreate, setOpenCreate] = useState(false);
  const client = useQueryClient();
  const readyQuery = useQuery({
    queryKey: ["workspace-ready"],
    queryFn: ({ signal }) => rpc<WorkspaceReady>("workspace/ready", {}, "ws-ready", signal),
  });
  const ready = readyQuery.error ? undefined : readyQuery.data?.value;
  const setupIssue = workspaceSetupIssue(ready);
  const hosted = ready?.deployment_mode === "HOSTED_REVIEW";
  const browserScopeId = ready?.browser_scope_id ?? null;
  const workspaceId = !hosted && validWorkspaceId(ready?.workspace_id) ? ready.workspace_id : null;
  const storageScope = useMemo<BrowserScope>(
    () => (hosted ? { mode: "HOSTED", browserScopeId } : { mode: "LOCAL", workspaceId }),
    [hosted, workspaceId, browserScopeId],
  );
  const scopeKey = ready
    ? hosted
      ? `HOSTED:${browserScopeId ?? "LEGACY"}`
      : `LOCAL:${workspaceId ?? "UNBOUND"}`
    : null;
  const projectsQuery = useQuery({
    queryKey: ["projects", scopeKey],
    enabled: Boolean(scopeKey),
    queryFn: ({ signal }) =>
      rpc<{ projects: ProjectSummary[] }>(
        "project/list",
        { project_id: "system:projects" },
        crypto.randomUUID(),
        signal,
      ),
  });
  const projects = useMemo(
    () => (projectsQuery.error ? [] : (projectsQuery.data?.value.projects ?? [])),
    [projectsQuery.error, projectsQuery.data],
  );
  const selection = useWorkspaceSelection(scopeKey, storageScope, client);
  const {
    context,
    page,
    setPage,
    restoredFor,
    contextStoreFailed,
    contextRestoreIssue,
    missingSelection,
    selectionVersion,
    rowOrigin,
    traceFocus,
    epoch,
    pendingAdmission,
    chooseProject,
    openProject,
    chooseThread,
    cancelOpenLatest,
    openTraceRow,
    admit,
    clearRowOrigin,
  } = selection;
  const project = projects.find((item) => item.project_id === context.projectId);
  const threadsQuery = useQuery({
    queryKey: ["threads", scopeKey, context.projectId],
    enabled: Boolean(scopeKey && project),
    queryFn: ({ signal }) =>
      rpc<{ threads: WorkThread[] }>(
        "thread/list",
        { project_id: context.projectId },
        crypto.randomUUID(),
        signal,
      ),
  });
  const threads = useMemo(
    () => (threadsQuery.error ? [] : (threadsQuery.data?.value.threads ?? [])),
    [threadsQuery.error, threadsQuery.data],
  );
  const { investigate } = useSelectionReconcile(selection, {
    projects,
    project,
    threads,
    projectsSettled: projectsQuery.isSuccess && !projectsQuery.isFetching,
    threadsSettled: threadsQuery.isSuccess && !threadsQuery.isFetching,
  });
  const renderedEpoch = epoch.current;
  const visibleProjects = projectsQuery.isFetching ? [] : projects;
  const visibleThreads = threadsQuery.isFetching ? [] : threads;
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const response = await fetch("/healthz", { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return (await response.json()) as { status: string; version: string };
    },
    retry: false,
    refetchInterval: 30000,
  });
  const shellSources = useProjectSources(project?.project_id ?? "", Boolean(project));
  const drawer = useNavigationDrawer();
  const management = useProjectManagement({
    projects,
    threads,
    onArchived: (target) => {
      if (context.projectId === target.project_id) chooseProject("");
    },
  });
  const gate = workspaceGate({
    readyQuery,
    ready,
    hosted,
    scopeKey,
    restoredFor,
    projectsQuery,
    projectsCount: projects.length,
    threadsQuery,
    hasProject: Boolean(project),
    threadId: context.threadId,
    threadListed: threads.some((item) => item.thread_id === context.threadId),
    admissionPending: pendingAdmission.current === context.threadId,
  });
  if (gate || !ready || !scopeKey) return gate;
  const executionReady = executionReadiness(ready, hosted);
  const sourceCount =
    project && shellSources.data ? connectedArtifacts(shellSources.data.value).length : null;
  return (
    <WorkspacePageContext.Provider value={page}>
      <main className="workstation conversation-workstation bp6-dark">
        <aside className="navigation-rail" {...drawer.railProps}>
          <Brand hosted={hosted} />
          <Button
            icon="plus"
            intent="primary"
            className="new-project-button"
            onClick={drawer.closing(() => {
              chooseProject("");
              setOpenCreate(true);
            })}
          >
            {t("workspace.newProject")}
          </Button>
          <InputGroup
            small
            leftIcon="search"
            aria-label={t("workspace.searchProjects")}
            placeholder={t("workspace.searchPlaceholder")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <section className="nav-section">
            <div className="nav-section-heading">
              <p className="nav-label">{t("nav.projects")}</p>
              <Button
                small
                minimal
                icon="refresh"
                aria-label={t("workspace.refreshProjects")}
                onClick={() => void projectsQuery.refetch()}
              />
            </div>
            {visibleProjects
              .filter((item) =>
                `${item.name} ${item.project_id}`.toLowerCase().includes(search.toLowerCase()),
              )
              .flatMap((item) => [
                <ProjectNavItem
                  project={item}
                  active={item.project_id === context.projectId}
                  removing={management.archiving}
                  key={item.project_id}
                  onOpen={drawer.closing(() => openProject(item.project_id))}
                  onRename={() => management.askRenameProject(item)}
                  onRemove={() => management.askArchive(item)}
                />,
                item.project_id === context.projectId ? (
                  <div className="project-sessions" key={`${item.project_id}-sessions`}>
                    <Button
                      small
                      minimal
                      icon="plus"
                      className="new-session-button"
                      onClick={drawer.closing(() => chooseThread(""))}
                    >
                      {t("workspace.newSession")}
                    </Button>
                    {threadsQuery.isPending && (
                      <p className="empty-copy">{t("workspace.loadingSessions")}</p>
                    )}
                    {threadsQuery.error && (
                      <Callout compact intent="danger" role="alert">
                        {threadsQuery.error.message}
                      </Callout>
                    )}
                    {visibleThreads.map((thread) => (
                      <ThreadNavItem
                        thread={thread}
                        active={thread.thread_id === context.threadId}
                        key={thread.thread_id}
                        onOpen={drawer.closing(() => chooseThread(thread.thread_id))}
                        onRename={() => management.askRenameThread(thread)}
                      />
                    ))}
                    {!threadsQuery.isPending && !threadsQuery.error && threads.length === 0 && (
                      <p className="empty-copy">{t("workspace.firstQuestion")}</p>
                    )}
                  </div>
                ) : null,
              ])}
            {!projectsQuery.isPending && !projectsQuery.error && projects.length === 0 && (
              <p className="empty-copy">{t("workspace.noProjects")}</p>
            )}
            <ArchivedProjects
              scopeKey={scopeKey}
              hosted={hosted}
              storageScope={storageScope}
              onDeleted={(projectId) => {
                if (context.projectId === projectId) chooseProject("");
              }}
            />
          </section>
          <div className="sidebar-bottom">
            <Button
              minimal
              alignText="left"
              icon={hosted ? "info-sign" : "settings"}
              onClick={drawer.closing(() => setPage(settingsPageFor(hosted)))}
            >
              {hosted ? t("workspace.operations") : t("settings.computerTitle")}
            </Button>
          </div>
        </aside>
        {drawer.open && <div {...drawer.backdropProps} />}
        <WorkspaceTopbar
          drawer={drawer}
          testMode={import.meta.env.VITE_THOTH_TEST_MODE === "true"}
          projectLabel={
            projectsQuery.isFetching
              ? t("workspace.checkingProject")
              : (project?.name ?? t("workspace.selectProject"))
          }
          threadLabel={
            context.threadId
              ? currentThreadLabel(threadsQuery.isFetching, visibleThreads, context.threadId)
              : null
          }
          cutoff={project ? projectCutoffText(project.cutoff_at) : null}
          health={health.data?.status === "ok" ? "ok" : health.error ? "failed" : "checking"}
          setupNeeded={!hosted && !executionReady}
          locationUnsaved={!hosted && contextStoreFailed}
          missingSelection={missingSelection}
          onOpenSettings={() => setPage(settingsPageFor(hosted))}
          onRefresh={() => void client.invalidateQueries()}
        />
        <div className="workspace-navigation">
          <WorkspaceLocationNotice location={ready.workspace_location} />
          <Tabs
            id={WORKSPACE_TABS_ID}
            selectedTabId={page}
            onChange={(value) => setPage(value as Page)}
          >
            <Tab id="research" title={t("workspace.conversation")} />
            <Tab id="resources" title={t("workspace.resources")} disabled={!project} />
            <Tab id="records" title={t("workspace.history")} disabled={!project} />
            <Tab id="trace" title={t("workspace.trace")} disabled={!project} />
            <Tab id="settings" title={t("workspace.settings")} disabled={!project && !hosted} />
          </Tabs>
          {project && (
            <WorkspaceCounts
              sessions={threadsQuery.isPending ? null : threads.length}
              sources={sourceCount}
            />
          )}
        </div>
        {project ? (
          <WorkspaceErrorBoundary
            resetKey={`${scopeKey}:${project.project_id}:${context.threadId}:${page}`}
            onRetry={() => client.invalidateQueries()}
          >
            <ProjectSession
              key={`${scopeKey}:${project.project_id}:${selectionVersion}`}
              project={project}
              threadId={context.threadId}
              epoch={epoch}
              page={page}
              onPage={setPage}
              hosted={hosted}
              workspaceId={workspaceId}
              browserScopeId={browserScopeId}
              executionReady={executionReady && !threadsQuery.error}
              consent={ready.setup?.internet_consent}
              modelConnected={ready.model_connected}
              readSuspended={
                readyQuery.isFetching || projectsQuery.isFetching || threadsQuery.isFetching
              }
              setupIssue={setupIssue}
              onUserInput={cancelOpenLatest}
              onAdmitted={admit}
              rowOrigin={rowOrigin}
              onOriginDone={clearRowOrigin}
              onInvestigate={investigate}
              traceFocus={traceFocus}
              onOpenTrace={openTraceRow}
            />
          </WorkspaceErrorBoundary>
        ) : isTimelineExampleRequested() && page === "research" ? (
          <TimelineExamplePane />
        ) : (
          <NoProjectPage
            page={page}
            hosted={hosted}
            workspaceId={workspaceId}
            storageScope={storageScope}
            consent={ready.setup?.internet_consent}
            missingSelection={missingSelection}
            contextStoreFailed={contextStoreFailed}
            landing={
              <ProjectLanding
                openCreate={openCreate}
                onOpenCreateHandled={() => setOpenCreate(false)}
                contextRestoreIssue={contextRestoreIssue}
                setupIssue={setupIssue}
                projects={visibleProjects}
                loading={projectsQuery.isPending || projectsQuery.isFetching}
                onOpen={openProject}
                onCreated={(projectId) => {
                  if (renderedEpoch === epoch.current) chooseProject(projectId, "resources");
                }}
              />
            }
          />
        )}
        {management.dialog}
      </main>
    </WorkspacePageContext.Provider>
  );
}
