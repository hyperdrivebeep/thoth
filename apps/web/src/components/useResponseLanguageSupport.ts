import { useQuery } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import { hasSubmissionStore } from "../api/submissionPersistence";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import type { WorkspaceReady } from "../api/research";

/** Whether this server accepts a response language on each request, and this browser can keep the pending request. */
export function useResponseLanguageSupport(scope: BrowserScope): boolean {
  const ready = useQuery({ queryKey: ["workspace-ready"], enabled: false, queryFn: ({ signal }) => rpc<WorkspaceReady>("workspace/ready", {}, "ws-ready", signal) });
  return hasSubmissionStore(scope) && ready.data?.value.response_language_scope === "REQUEST" && ready.data.value.response_languages?.includes("en") === true;
}
