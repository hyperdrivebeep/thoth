import {
  inspectLocalContext,
  writeLocalContext,
  type BrowserScope,
} from "./localWorkspacePersistence";

const localPrefix = "thoth:local-workspace:v1:";
const sessionPrefixes = ["thoth:draft:v1:", "thoth:restore-pending:v1:"];

function keysOf(storage: Storage): string[] {
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) keys.push(key);
  }
  return keys;
}

function parsedList(raw: string): unknown[] | null {
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

/**
 * After a project is deleted on the server, drop what this browser remembers about it: the last
 * opened place, unsent drafts and unconfirmed requests. Browser convenience only; the server owns the data.
 */
export function forgetProject(scope: BrowserScope, projectId: string): void {
  try {
    if (scope.mode === "LOCAL" && scope.workspaceId) {
      const context = inspectLocalContext(scope.workspaceId);
      if (context.kind === "VALID" && context.context.projectId === projectId)
        writeLocalContext(scope.workspaceId, { projectId: "", threadId: "" });
      for (const key of keysOf(localStorage)) {
        const kind = ["draft:", "pending:"].find((name) => key.startsWith(localPrefix + name));
        const identity = kind ? parsedList(key.slice(localPrefix.length + kind.length)) : null;
        if (identity && identity[0] === scope.workspaceId && identity[1] === projectId)
          localStorage.removeItem(key);
      }
    }
    for (const key of keysOf(sessionStorage)) {
      const prefix = sessionPrefixes.find((name) => key.startsWith(name));
      const identity = prefix ? parsedList(key.slice(prefix.length)) : null;
      if (identity && identity[0] === projectId) sessionStorage.removeItem(key);
    }
  } catch {
    /* Storage can be unavailable; nothing the server holds depends on it. */
  }
}
