import { useSyncExternalStore } from "react";
import type { BrowserScope, ResponseLanguage } from "./localWorkspacePersistence";

/**
 * The default response language of one browser scope (a local workspace, or a hosted browser
 * session): one value for the whole scope, not one per conversation. null means automatic: the
 * request carries no response_language and the server follows the language of the question. A
 * fixed language is sent on every new request. The older per-conversation keys
 * (thoth:draft-language:v1) are neither read nor removed.
 */
export type ResponseLanguageDefault = ResponseLanguage | null;

const listeners = new Set<() => void>();
// Only holds a choice the browser could not store (or a scope with no identity yet), for this tab.
const unsaved = new Map<string, ResponseLanguageDefault>();

function identity(scope: BrowserScope): string | null {
  return scope.mode === "LOCAL" ? scope.workspaceId : scope.browserScopeId ?? null;
}
function memoryKey(scope: BrowserScope): string { return `${scope.mode}:${identity(scope)}`; }
function storageKey(scope: BrowserScope): string | null {
  const id = identity(scope);
  return id ? `thoth:response-language-default:v1:${JSON.stringify([id])}` : null;
}
function store(scope: BrowserScope): Storage | null {
  try { return scope.mode === "LOCAL" ? localStorage : sessionStorage; } catch { return null; }
}

export function readResponseLanguageDefault(scope: BrowserScope): ResponseLanguageDefault {
  const held = unsaved.get(memoryKey(scope));
  if (held !== undefined) return held;
  const name = storageKey(scope);
  if (!name) return null;
  try {
    const value = store(scope)?.getItem(name);
    return value === "ko" || value === "en" ? value : null;
  } catch { return null; }
}

/** Returns false when the browser could not keep the choice; it then lasts until the tab closes. */
export function writeResponseLanguageDefault(scope: BrowserScope, value: ResponseLanguageDefault): boolean {
  let saved = false;
  const name = storageKey(scope);
  try {
    const target = store(scope);
    if (name && target) { target.setItem(name, value ?? "auto"); saved = true; }
  } catch { saved = false; }
  if (saved) unsaved.delete(memoryKey(scope)); else unsaved.set(memoryKey(scope), value);
  listeners.forEach(listener => listener());
  return saved;
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  const onStorage = () => listener();
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); if (typeof window !== "undefined") window.removeEventListener("storage", onStorage); };
};

export function useResponseLanguageDefault(scope: BrowserScope): ResponseLanguageDefault {
  return useSyncExternalStore(subscribe, () => readResponseLanguageDefault(scope), () => null);
}
