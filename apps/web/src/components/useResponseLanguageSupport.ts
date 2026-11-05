import { useQuery } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import { hasSubmissionStore } from "../api/submissionPersistence";
import { knownResponseLanguages, type ResponseLanguage } from "../api/responseLanguages";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import type { WorkspaceReady } from "../api/research";

/** The response languages this server accepts on a request (none when it takes no per-request language, or this browser cannot keep the pending request). */
export function useSupportedResponseLanguages(scope: BrowserScope): ResponseLanguage[] {
  const ready = useQuery({ queryKey: ["workspace-ready"], enabled: false, queryFn: ({ signal }) => rpc<WorkspaceReady>("workspace/ready", {}, "ws-ready", signal) });
  if (!hasSubmissionStore(scope) || ready.data?.value.response_language_scope !== "REQUEST") return [];
  return knownResponseLanguages(ready.data.value.response_languages);
}

/** Whether this server accepts the given response language (any fixed language when none is given). */
export function useResponseLanguageSupport(scope: BrowserScope, language: ResponseLanguage | null = null): boolean {
  const supported = useSupportedResponseLanguages(scope);
  return language === null ? supported.length > 0 : supported.includes(language);
}