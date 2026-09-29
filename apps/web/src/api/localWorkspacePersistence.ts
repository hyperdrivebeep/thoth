/** Browser convenience only. Research records and permissions remain server-owned. */
export type BrowserScope = { mode: "LOCAL"; workspaceId: string | null } | { mode: "HOSTED" };
export type WorkspaceContext = { projectId: string; threadId: string };
export type ContextReadResult = { kind: "MISSING" | "VALID" | "CORRUPT" | "UNAVAILABLE"; context: WorkspaceContext };
export type DraftWriteResult = "SAVED" | "TOO_LARGE" | "UNAVAILABLE";
export type DraftReadResult = { kind: "MISSING" | "VALID" | "CORRUPT" | "UNAVAILABLE"; text: string };
export type PendingModelSelection = { provider: string; model: string; reasoning_effort?: string };
export type PendingSubmission = { version: 1; workspaceId: string; projectId: string; threadId: string;
  problem: string; selection: PendingModelSelection | null; signature: string; key: string; createdAt: number };
export type PendingRead = { kind: "NONE" } | { kind: "PENDING"; value: PendingSubmission } | { kind: "INVALID" };

const prefix = "thoth:local-workspace:v1:";
const maxDraftLength = 20_000;
const maxPendingLength = 50_000;
const maxIdentityLength = 160;
const emptyContext: WorkspaceContext = { projectId: "", threadId: "" };

export function validWorkspaceId(value: string | null | undefined): value is string {
  return typeof value === "string" && value.length >= 8 && value.length <= maxIdentityLength && /^[a-zA-Z0-9:_-]+$/.test(value);
}

function validRecordId(value: unknown): value is string {
  return typeof value === "string" && value.length <= maxIdentityLength && Array.from(value).every(char => char.charCodeAt(0) >= 32);
}

function contextKey(workspaceId: string) { return `${prefix}context:${workspaceId}`; }
function draftKey(workspaceId: string, projectId: string, threadId: string) {
  return `${prefix}draft:${JSON.stringify([workspaceId, projectId, threadId])}`;
}
function pendingKey(workspaceId: string, projectId: string, threadId: string) {
  return `${prefix}pending:${JSON.stringify([workspaceId, projectId, threadId])}`;
}

export function submissionSignature(projectId: string, threadId: string, problem: string, selection: PendingModelSelection | null): string {
  return JSON.stringify([projectId, threadId, problem, selection ? [selection.provider, selection.model, selection.reasoning_effort ?? null] : null]);
}

export function inspectLocalContext(workspaceId: string | null): ContextReadResult {
  if (!validWorkspaceId(workspaceId)) return { kind: "UNAVAILABLE", context: { ...emptyContext } };
  let raw: string | null;
  try { raw = localStorage.getItem(contextKey(workspaceId)); }
  catch { return { kind: "UNAVAILABLE", context: { ...emptyContext } }; }
  if (raw === null) return { kind: "MISSING", context: { ...emptyContext } };
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object" && "version" in value && value.version === 1 &&
      "workspaceId" in value && value.workspaceId === workspaceId &&
      "projectId" in value && validRecordId(value.projectId) &&
      "threadId" in value && validRecordId(value.threadId)) {
      return { kind: "VALID", context: { projectId: value.projectId, threadId: value.threadId } };
    }
  } catch { /* Preserve the raw record for an explicit user choice. */ }
  return { kind: "CORRUPT", context: { ...emptyContext } };
}

export function readLocalContext(workspaceId: string | null): WorkspaceContext {
  return inspectLocalContext(workspaceId).context;
}

export function writeLocalContext(workspaceId: string | null, context: WorkspaceContext): boolean {
  if (!validWorkspaceId(workspaceId) || !validRecordId(context.projectId) || !validRecordId(context.threadId)) return false;
  try {
    localStorage.setItem(contextKey(workspaceId), JSON.stringify({ version: 1, workspaceId, ...context }));
    return true;
  } catch { return false; }
}

export function inspectLocalDraft(workspaceId: string | null, projectId: string, threadId: string): DraftReadResult {
  if (!validWorkspaceId(workspaceId) || !validRecordId(projectId) || !validRecordId(threadId)) return { kind: "UNAVAILABLE", text: "" };
  let raw: string | null;
  try { raw = localStorage.getItem(draftKey(workspaceId, projectId, threadId)); }
  catch { return { kind: "UNAVAILABLE", text: "" }; }
  if (raw === null) return { kind: "MISSING", text: "" };
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object" && "version" in value && value.version === 1 &&
      "workspaceId" in value && value.workspaceId === workspaceId &&
      "projectId" in value && value.projectId === projectId &&
      "threadId" in value && value.threadId === threadId &&
      "text" in value && typeof value.text === "string" && value.text.length <= maxDraftLength) return { kind: "VALID", text: value.text };
  } catch { /* Keep the current editor usable and preserve the raw record. */ }
  return { kind: "CORRUPT", text: "" };
}

export function readLocalDraft(workspaceId: string | null, projectId: string, threadId: string): string {
  return inspectLocalDraft(workspaceId, projectId, threadId).text;
}

export function writeLocalDraft(workspaceId: string | null, projectId: string, threadId: string, text: string): DraftWriteResult {
  if (text.length > maxDraftLength) return "TOO_LARGE";
  if (!validWorkspaceId(workspaceId) || !validRecordId(projectId) || !validRecordId(threadId)) return "UNAVAILABLE";
  try {
    const key = draftKey(workspaceId, projectId, threadId);
    if (text) localStorage.setItem(key, JSON.stringify({ version: 1, workspaceId, projectId, threadId, text }));
    else localStorage.removeItem(key);
    return "SAVED";
  } catch { return "UNAVAILABLE"; }
}

export function readLocalPending(workspaceId: string | null, projectId: string, threadId: string): PendingRead {
  if (!validWorkspaceId(workspaceId) || !projectId || !validRecordId(projectId) || !validRecordId(threadId)) return { kind: "NONE" };
  try {
    const raw = localStorage.getItem(pendingKey(workspaceId, projectId, threadId));
    if (raw === null) return { kind: "NONE" };
    if (raw.length > maxPendingLength) return { kind: "INVALID" };
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || !("version" in value) || value.version !== 1 ||
      !Object.keys(value).every(key => ["version", "workspaceId", "projectId", "threadId", "problem", "selection", "signature", "key", "createdAt"].includes(key)) ||
      !("workspaceId" in value) || value.workspaceId !== workspaceId ||
      !("projectId" in value) || value.projectId !== projectId ||
      !("threadId" in value) || value.threadId !== threadId ||
      !("problem" in value) || typeof value.problem !== "string" || value.problem.length > maxDraftLength ||
      !("selection" in value) || !(value.selection === null || validPendingSelection(value.selection)) ||
      !("signature" in value) || typeof value.signature !== "string" ||
      !("key" in value) || typeof value.key !== "string" || !/^[a-f0-9-]{36}$/i.test(value.key) ||
      !("createdAt" in value) || typeof value.createdAt !== "number" || !Number.isFinite(value.createdAt)) return { kind: "INVALID" };
    const pending = value as PendingSubmission;
    return pending.signature === submissionSignature(projectId, threadId, pending.problem, pending.selection)
      ? { kind: "PENDING", value: pending } : { kind: "INVALID" };
  } catch { return { kind: "INVALID" }; }
}

function validPendingSelection(value: unknown): value is PendingModelSelection {
  return Boolean(value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).every(key => ["provider", "model", "reasoning_effort"].includes(key)) &&
    "provider" in value && typeof value.provider === "string" && value.provider.length > 0 && value.provider.length <= 160 &&
    "model" in value && typeof value.model === "string" && value.model.length > 0 && value.model.length <= 160 &&
    (!("reasoning_effort" in value) || typeof value.reasoning_effort === "string" && value.reasoning_effort.length <= 32));
}

export function writeLocalPending(value: PendingSubmission): DraftWriteResult {
  if (value.problem.length > maxDraftLength) return "TOO_LARGE";
  if (!validWorkspaceId(value.workspaceId) || !value.projectId || !validRecordId(value.projectId) || !validRecordId(value.threadId) ||
    !(value.selection === null || validPendingSelection(value.selection)) ||
    value.signature !== submissionSignature(value.projectId, value.threadId, value.problem, value.selection)) return "UNAVAILABLE";
  const raw = JSON.stringify(value);
  if (raw.length > maxPendingLength) return "TOO_LARGE";
  try { localStorage.setItem(pendingKey(value.workspaceId, value.projectId, value.threadId), raw); return "SAVED"; }
  catch { return "UNAVAILABLE"; }
}

export function clearLocalPending(workspaceId: string | null, projectId: string, threadId: string): boolean {
  if (!validWorkspaceId(workspaceId) || !validRecordId(projectId) || !validRecordId(threadId)) return false;
  try { localStorage.removeItem(pendingKey(workspaceId, projectId, threadId)); return true; }
  catch { return false; }
}
