import type { BrowserScope, ResponseLanguage } from "./localWorkspacePersistence";

function key(scope: BrowserScope, project: string, thread: string): string | null {
  const identity = scope.mode === "LOCAL" ? scope.workspaceId : scope.browserScopeId;
  return identity ? `thoth:draft-language:v1:${JSON.stringify([identity, project, thread])}` : null;
}

/** Undefined means inherit the prior accepted request; null is an explicit legacy choice. */
export function readDraftLanguage(scope: BrowserScope, project: string, thread: string): ResponseLanguage | null | undefined {
  const name = key(scope, project, thread);
  if (!name) return undefined;
  try {
    const value = (scope.mode === "LOCAL" ? localStorage : sessionStorage).getItem(name);
    return value === "ko" || value === "en" ? value : value === "auto" ? null : undefined;
  } catch { return undefined; }
}

export function writeDraftLanguage(scope: BrowserScope, project: string, thread: string, language: ResponseLanguage | null): boolean {
  const name = key(scope, project, thread);
  if (!name) return scope.mode === "HOSTED";
  try { (scope.mode === "LOCAL" ? localStorage : sessionStorage).setItem(name, language ?? "auto"); return true; } catch { return false; }
}
