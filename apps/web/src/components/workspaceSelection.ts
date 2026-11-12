import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { readResponseLanguageDefault } from "../api/responseLanguageDefault";
import { readWorkspaceContext, saveWorkspaceContext } from "../api/research";
import {
  inspectLocalContext,
  type BrowserScope,
  type PendingOrigin,
} from "../api/localWorkspacePersistence";
import { readDraft, writeDraft } from "../api/conversation";
import { fillQuestion, threadForRow, type RowInvestigation } from "./traceInvestigation";
import type { WorkThread } from "../types";

/** "settings" is the open project's settings; "computer" is this computer's (accounts, language, internet, local search parts). */
export type Page =
  | "research"
  | "resources"
  | "records"
  | "trace"
  | "raw-records"
  | "settings"
  | "computer"
  | "capabilities";
/** Which settings screen a link opens. The hosted review environment has one screen for both. */
export const settingsPageFor = (hosted: boolean, target?: "consent" | "project"): Page =>
  hosted || target === "project" ? "settings" : "computer";
export type TraceRowRef = { kind: string; id: string };
/** The trace row a question was started from: held here until it is sent, so the next request carries it. */
export type RowOrigin = { threadId: string; origin: PendingOrigin };

/**
 * Which project and thread the workspace shows, restored per workspace scope and saved back. The shell renders; this
 * hook owns the selection. Checks against the project and thread lists happen in useSelectionReconcile, after the
 * shell has read those lists for the selected project.
 */
export function useWorkspaceSelection(
  scopeKey: string | null,
  storageScope: BrowserScope,
  client: QueryClient,
) {
  const [context, setContext] = useState({ projectId: "", threadId: "" });
  const [restoredFor, setRestoredFor] = useState<string | null>(null);
  const [contextStoreFailed, setContextStoreFailed] = useState(false);
  const [contextRestoreIssue, setContextRestoreIssue] = useState<"CORRUPT" | "UNAVAILABLE" | null>(
    null,
  );
  const [missingSelection, setMissingSelection] = useState<"project" | "thread" | null>(null);
  const [page, setPage] = useState<Page>("research");
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [rowOrigin, setRowOrigin] = useState<RowOrigin | null>(null);
  const [traceFocus, setTraceFocus] = useState<TraceRowRef | null>(null);
  const epoch = useRef(0);
  const previousScope = useRef<string | null>(null);
  const pendingAdmission = useRef<string | null>(null);
  const openLatestWork = useRef(false);
  useEffect(() => {
    if (!scopeKey || restoredFor === scopeKey) return;
    if (previousScope.current !== scopeKey) {
      client.removeQueries({
        predicate: (query) =>
          !["workspace-ready", "health"].includes(String(query.queryKey[0])) &&
          !query.queryKey.includes(scopeKey),
      });
    }
    previousScope.current = scopeKey;
    const restored =
      storageScope.mode === "LOCAL" ? inspectLocalContext(storageScope.workspaceId) : null;
    setContext(restored?.context ?? readWorkspaceContext(storageScope));
    setContextRestoreIssue(
      restored?.kind === "CORRUPT" || restored?.kind === "UNAVAILABLE" ? restored.kind : null,
    );
    setContextStoreFailed(false);
    setRestoredFor(scopeKey);
    setMissingSelection(null);
    setPage("research");
    epoch.current += 1;
    setSelectionVersion((value) => value + 1);
  }, [scopeKey, restoredFor, storageScope, client]);
  useEffect(() => {
    if (!scopeKey || restoredFor !== scopeKey || contextRestoreIssue) return;
    const saved = saveWorkspaceContext(context.projectId, context.threadId, storageScope);
    if (storageScope.mode === "LOCAL") setContextStoreFailed(!saved);
  }, [scopeKey, restoredFor, context, storageScope, contextRestoreIssue]);
  const chooseProject = (projectId: string, nextPage: Page = "research") => {
    setRowOrigin(null);
    setTraceFocus(null);
    epoch.current += 1;
    pendingAdmission.current = null;
    setMissingSelection(null);
    setContextRestoreIssue(null);
    setSelectionVersion((v) => v + 1);
    setContext({ projectId, threadId: "" });
    setPage(projectId ? nextPage : "research");
  };
  const openProject = (projectId: string) => {
    if (projectId === context.projectId) {
      setPage("research");
      return;
    }
    chooseProject(projectId);
    openLatestWork.current = Boolean(projectId);
  };
  const chooseThread = (threadId: string) => {
    setRowOrigin(null);
    openLatestWork.current = false;
    epoch.current += 1;
    pendingAdmission.current = null;
    setMissingSelection(null);
    setContextRestoreIssue(null);
    setSelectionVersion((v) => v + 1);
    setContext((current) => ({ ...current, threadId }));
    setPage("research");
  };
  const cancelOpenLatest = useCallback(() => {
    openLatestWork.current = false;
  }, []);
  const openTraceRow = (row: TraceRowRef) => {
    setTraceFocus(row);
    setPage("trace");
  };
  const admit = (threadId: string) => {
    pendingAdmission.current = threadId;
    setContext((current) => ({ ...current, threadId }));
  };
  const clearRowOrigin = () => setRowOrigin(null);
  return {
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
    epoch: epoch as RefObject<number>,
    pendingAdmission,
    chooseProject,
    openProject,
    chooseThread,
    cancelOpenLatest,
    openTraceRow,
    admit,
    clearRowOrigin,
    internals: {
      scopeKey,
      storageScope,
      client,
      setContext,
      setMissingSelection,
      setSelectionVersion,
      setRowOrigin,
      openLatestWork,
    },
  };
}

export type WorkspaceSelection = ReturnType<typeof useWorkspaceSelection>;

type Lists = {
  projects: { project_id: string }[];
  project: { project_id: string } | undefined;
  threads: WorkThread[];
  projectsSettled: boolean;
  threadsSettled: boolean;
};

/**
 * What happens when the chosen project or thread is no longer listed, landing on the latest work after opening a
 * project, and "원인 조사" from a trace row (open that row's conversation with the question filled in; nothing is sent).
 */
export function useSelectionReconcile(
  selection: WorkspaceSelection,
  { projects, project, threads, projectsSettled, threadsSettled }: Lists,
) {
  const { context, restoredFor, epoch, pendingAdmission, chooseThread } = selection;
  const {
    scopeKey,
    storageScope,
    client,
    setContext,
    setMissingSelection,
    setSelectionVersion,
    setRowOrigin,
    openLatestWork,
  } = selection.internals;
  useEffect(() => {
    if (!scopeKey || restoredFor !== scopeKey) return;
    if (
      context.projectId &&
      projectsSettled &&
      !projects.some((item) => item.project_id === context.projectId)
    ) {
      client.removeQueries({ predicate: (query) => query.queryKey.includes(context.projectId) });
      epoch.current += 1;
      pendingAdmission.current = null;
      setSelectionVersion((value) => value + 1);
      setContext({ projectId: "", threadId: "" });
      setMissingSelection("project");
      return;
    }
    if (
      pendingAdmission.current &&
      threads.some((item) => item.thread_id === pendingAdmission.current)
    )
      pendingAdmission.current = null;
    if (
      context.threadId &&
      project &&
      threadsSettled &&
      !threads.some((item) => item.thread_id === context.threadId) &&
      pendingAdmission.current !== context.threadId
    ) {
      client.removeQueries({ predicate: (query) => query.queryKey.includes(context.threadId) });
      epoch.current += 1;
      pendingAdmission.current = null;
      setSelectionVersion((value) => value + 1);
      setContext({ projectId: context.projectId, threadId: "" });
      setMissingSelection("thread");
    }
  }, [
    scopeKey,
    restoredFor,
    context,
    projects,
    project,
    projectsSettled,
    threads,
    threadsSettled,
    client,
    epoch,
    pendingAdmission,
    setContext,
    setMissingSelection,
    setSelectionVersion,
  ]);
  // Opening a project lands on its most recently updated work instead of an empty question screen.
  useEffect(() => {
    if (!openLatestWork.current || !project || context.threadId || !threadsSettled) return;
    openLatestWork.current = false;
    const latest = threads.reduce<WorkThread | null>(
      (best, item) => (!best || (item.updated_at ?? "") > (best.updated_at ?? "") ? item : best),
      null,
    );
    if (latest) chooseThread(latest.thread_id);
  }, [project, context.threadId, threads, threadsSettled]); // eslint-disable-line react-hooks/exhaustive-deps -- same deps as before the split; chooseThread is recreated each render
  const investigate = (item: RowInvestigation) => {
    if (!project) return;
    const target =
      threadForRow(threads, item.origin.subject_kind, item.origin.subject_id)?.thread_id ?? "";
    const draft = readDraft(project.project_id, target, storageScope);
    // The question is written in the response language set in Settings (the question's own language when automatic).
    const question = item.questionForLanguage
      ? item.questionForLanguage(readResponseLanguageDefault(storageScope))
      : item.question;
    writeDraft(project.project_id, target, fillQuestion(draft, question), storageScope);
    chooseThread(target);
    setRowOrigin({ threadId: target, origin: item.origin });
  };
  return { investigate };
}
