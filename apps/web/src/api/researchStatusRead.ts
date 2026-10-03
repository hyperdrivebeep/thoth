import type { QueryClient } from "@tanstack/react-query";
import type { ResearchStatus } from "./research";
import { rpc, type RpcResult } from "./rpcClient";

/**
 * Reads a thread's status. While the last read said the operation is running, polling asks for the
 * progress view only (no stored result, no criteria projection) and lays it over the last full read;
 * the first read that no longer says running is a full one, so the result appears exactly once.
 */
export async function readResearchStatus(
  client: QueryClient, queryKey: readonly unknown[], projectId: string, threadId: string, signal?: AbortSignal,
): Promise<RpcResult<ResearchStatus>> {
  const prior = client.getQueryData<RpcResult<ResearchStatus>>(queryKey);
  if (prior?.value.operation_state === "RUNNING") {
    const light = await rpc<ResearchStatus>("thread/read", { project_id: projectId, thread_id: threadId, view: "PROGRESS" }, crypto.randomUUID(), signal);
    if (light.value.operation_state === "RUNNING") return { ...light, value: { ...prior.value, ...light.value } };
  }
  return rpc<ResearchStatus>("thread/read", { project_id: projectId, thread_id: threadId }, crypto.randomUUID(), signal);
}

