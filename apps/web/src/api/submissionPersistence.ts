import { clearLocalPending, readLocalPending, validWorkspaceId, writeLocalPending,
  type BrowserScope, type DraftWriteResult, type PendingRead, type PendingSubmission } from "./localWorkspacePersistence";

export function submissionScopeId(scope: BrowserScope): string | null {
  return scope.mode === "LOCAL" ? scope.workspaceId : scope.browserScopeId ?? null;
}
export function hasSubmissionStore(scope: BrowserScope): boolean {
  return scope.mode === "LOCAL" || validWorkspaceId(scope.browserScopeId);
}
function hostedStore(): Storage | null { try { return sessionStorage; } catch { return null; } }

export function readSubmission(scope: BrowserScope, project: string, thread: string): PendingRead {
  if (scope.mode === "LOCAL") return readLocalPending(scope.workspaceId, project, thread);
  if (!hasSubmissionStore(scope)) return {kind: "NONE"};
  const storage = hostedStore();
  return storage ? readLocalPending(scope.browserScopeId ?? null, project, thread, storage) : {kind: "INVALID"};
}
export function writeSubmission(scope: BrowserScope, value: PendingSubmission): DraftWriteResult {
  if (value.workspaceId !== submissionScopeId(scope)) return "UNAVAILABLE";
  if (scope.mode === "LOCAL") return writeLocalPending(value);
  const storage = hostedStore();
  return storage ? writeLocalPending(value, storage) : "UNAVAILABLE";
}
export function clearSubmission(scope: BrowserScope, project: string, thread: string): boolean {
  if (scope.mode === "LOCAL") return clearLocalPending(scope.workspaceId, project, thread);
  const storage = hostedStore();
  return storage ? clearLocalPending(scope.browserScopeId ?? null, project, thread, storage) : false;
}
