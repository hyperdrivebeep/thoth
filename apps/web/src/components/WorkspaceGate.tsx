import { t } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { WorkspaceReady } from "../api/research";
import { FirstRunSetup } from "./FirstRunSetup";

type Shell = { readyQuery: UseQueryResult<unknown>; ready: WorkspaceReady | undefined; hosted: boolean; scopeKey: string | null; restoredFor: string | null;
  projectsQuery: UseQueryResult<unknown>; projectsCount: number; threadsQuery: UseQueryResult<unknown>; hasProject: boolean; threadId: string; threadListed: boolean; admissionPending: boolean };

const shell = (children: ReactNode) => <main className="first-run-shell bp6-dark">{children}</main>;
const status = (text: string) => shell(<p className="empty-copy" role="status">{text}</p>);
const card = (children: ReactNode) => shell(<section className="first-run-card">{children}</section>);

type ReadyShell = Pick<Shell, "readyQuery" | "ready" | "hosted" | "scopeKey" | "restoredFor" | "projectsQuery" | "projectsCount">;

/** The readiness read itself: still pending, failed, or not yet restored for this workspace scope. */
function readyReadGate({ readyQuery, ready, scopeKey, restoredFor }: ReadyShell): ReactNode | null {
  if (readyQuery.isPending || (readyQuery.isSuccess && !ready)) return status(t("workspace.preparing"));
  if (readyQuery.error) return card(<Callout intent="danger" title={t("workspace.readinessFailed")}>{t("workspace.readinessAdvice")}<Button small onClick={()=>void readyQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout>);
  if (!ready || !scopeKey || restoredFor !== scopeKey) return status(t("workspace.loadingSaved"));
  return null;
}

/** First-run setup, the project list read, and an unreadable workspace; runs after the readiness read settled. */
function readinessGate(shell: ReadyShell): ReactNode | null {
  const pending = readyReadGate(shell);
  if (pending || !shell.ready) return pending;
  const { readyQuery, ready, hosted, projectsQuery, projectsCount } = shell;
  if (hosted && !ready.ready) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  if (projectsQuery.isPending && !projectsQuery.data) return status(t("workspace.checkingProjects"));
  if (projectsQuery.error) return card(<Callout intent="warning" role="alert">{t("workspace.accessFailed")}<Button small onClick={()=>void projectsQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout>);
  const workspaceReadable = hosted || ready.workspace_readable !== false;
  const setupComplete = ready.setup_complete ?? (ready.setup?.internet_consent !== undefined && ready.setup.internet_consent !== "UNDECIDED");
  if (!workspaceReadable) return card(<Callout intent="warning" role="alert">{t("workspace.savedUnavailable")}<Button small onClick={()=>void readyQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout>);
  if (!hosted && ready.workspace_readable === undefined && !ready.ready) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  if (!hosted && !setupComplete && projectsCount === 0) return <FirstRunSetup onDone={()=>void readyQuery.refetch()}/>;
  return null;
}

/** The saved thread selection is still being checked against the project's thread list. */
function threadGate({ threadsQuery, hasProject, threadId, threadListed, admissionPending }: Shell): ReactNode | null {
  if (!threadId || !hasProject) return null;
  if (threadsQuery.isPending && !threadsQuery.data) return status(t("workspace.checkingAccess"));
  if (threadsQuery.error) return card(<Callout intent="warning" role="alert">{t("workspace.taskUnavailable")}<Button small onClick={()=>void threadsQuery.refetch()}>{t("workspace.checkAgain")}</Button></Callout>);
  if (!threadListed && !admissionPending) return status(t("workspace.selectionCheck"));
  return null;
}

/**
 * The screens that come before the workspace can be shown. Returns null when the workspace itself should render.
 */
export function workspaceGate(shell: Shell): ReactNode | null {
  return readinessGate(shell) ?? threadGate(shell);
}

/** Whether new research may run: the server's execution flag (or readiness when hosted). */
export function executionReadiness(ready: WorkspaceReady, hosted: boolean): boolean {
  return hosted ? ready.ready : (ready.execution_ready ?? ready.ready);
}
