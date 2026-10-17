import { hashKey, type QueryClient } from "@tanstack/react-query";
import type { ResearchStatus, WorkspaceReady } from "./research";
import { rpc, type RpcResult } from "./rpcClient";
import { isReadAccessDenied } from "./readAccess";

/**
 * Reads a thread's status. While the last read said the operation is running, polling asks for the
 * progress view only (no stored result, no criteria projection) and lays it over the last full read;
 * the first read that no longer says running is a full one, so the result appears exactly once.
 */
export async function readResearchStatus(
  client: QueryClient, queryKey: readonly unknown[], projectId: string, threadId: string, signal?: AbortSignal,
): Promise<RpcResult<ResearchStatus>> {
  const ready = client.getQueryData<RpcResult<WorkspaceReady>>(["workspace-ready"]);
  const contract = ready?.value.response_language_scope === "REQUEST" ? { expected_result_contract: "v4" } : {};
  const queryHash = hashKey(queryKey);
  // Same key contract as useReadAccessProtection; the query's real success releases it.
  const guardKey = ["read-access-blocked", queryHash];
  const owner = client.getQueryCache().get(queryHash);
  const requiresFullRead = () => client.getQueryData<boolean>(guardKey) === true
    || isReadAccessDenied(client.getQueryState(queryKey)?.error);
  try {
    const prior = requiresFullRead() ? undefined : client.getQueryData<RpcResult<ResearchStatus>>(queryKey);
    if (prior?.value.operation_state === "RUNNING") {
      const light = await rpc<ResearchStatus>("thread/read", { project_id: projectId, thread_id: threadId, ...contract, view: "PROGRESS" }, crypto.randomUUID(), signal);
      if (light.value.operation_state === "RUNNING" && !requiresFullRead()) return { ...light, value: { ...prior.value, ...light.value } };
    }
    return await rpc<ResearchStatus>("thread/read", { project_id: projectId, thread_id: threadId, ...contract }, crypto.randomUUID(), signal);
  } catch (error) {
    if (isReadAccessDenied(error) && client.getQueryCache().get(queryHash) === owner) {
      client.setQueryData(guardKey, true);
    }
    throw error;
  }
}

