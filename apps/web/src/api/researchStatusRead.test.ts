import { QueryClient } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import type { ResearchStatus } from "./research";

const calls: { method: string; input: Record<string, unknown> }[] = [];
const replies: Record<string, unknown>[] = [];
vi.mock("./rpcClient", () => ({
  rpc: async (method: string, input: Record<string, unknown>) => {
    calls.push({ method, input });
    const value = replies.shift();
    if (!value) throw new Error("no reply queued");
    return { operation_id: "", state: "SUCCEEDED", value };
  },
}));
const { readResearchStatus } = await import("./researchStatusRead");

const key = ["research", "p", "t"];
const status = (patch: Record<string, unknown>) => ({ operation_state: "SUCCEEDED", usage: { total_tokens: 1 }, ...patch });
let client: QueryClient;
beforeEach(() => { calls.length = 0; replies.length = 0; client = new QueryClient(); });

it("reads in full when nothing is running", async () => {
  replies.push(status({ current_result: { operation_id: "op" } }));
  const result = await readResearchStatus(client, key, "p", "t");
  expect(calls).toEqual([{ method: "thread/read", input: { project_id: "p", thread_id: "t" } }]);
  expect((result.value as ResearchStatus).current_result).toBeTruthy();
});

it("polls the light progress view while running and keeps the earlier full fields", async () => {
  client.setQueryData(key, { operation_id: "", state: "SUCCEEDED", value: status({ operation_state: "RUNNING", coverage_matrix: { rows: [1] }, usage: { total_tokens: 1 } }) });
  replies.push(status({ operation_state: "RUNNING", usage: { total_tokens: 99 } }));
  const result = await readResearchStatus(client, key, "p", "t");
  expect(calls).toEqual([{ method: "thread/read", input: { project_id: "p", thread_id: "t", view: "PROGRESS" } }]);
  expect(result.value.operation_state).toBe("RUNNING");
  expect((result.value.usage as { total_tokens: number }).total_tokens).toBe(99);
  expect((result.value as Record<string, unknown>).coverage_matrix).toEqual({ rows: [1] });
});

it("reads in full once, right after the light read says the run ended", async () => {
  client.setQueryData(key, { operation_id: "", state: "SUCCEEDED", value: status({ operation_state: "RUNNING" }) });
  replies.push(status({ operation_state: "SUCCEEDED" }), status({ operation_state: "SUCCEEDED", current_result: { operation_id: "op" } }));
  const result = await readResearchStatus(client, key, "p", "t");
  expect(calls.map(call => call.input.view)).toEqual(["PROGRESS", undefined]);
  expect((result.value as ResearchStatus).current_result).toBeTruthy();
});

