import { QueryClient } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { readConversation, withCurrentCheckpoint, type ConversationPage } from "./conversation";
import { canRetainConversation } from "./conversationRefresh";
import { rpc, RpcError, RpcTransportError } from "./rpcClient";

vi.mock("./rpcClient", async importOriginal => ({
  ...await importOriginal<typeof import("./rpcClient")>(), rpc: vi.fn(),
}));

const clients: QueryClient[] = [];
afterEach(() => { clients.splice(0).forEach(client => client.clear()); vi.resetAllMocks(); });

async function readableAnswerThen(error: Error) {
  let interrupted = false;
  vi.mocked(rpc).mockImplementation(async method => {
    if (method === "thread/activity/list") return { operation_id: "query", state: "SUCCEEDED", value: {
      conversation: { turns: [{ request_epoch: 1, request_revision_digest: "request-1", operation_id: "op-1",
        text: "saved question", edit_kind: "APPEND", created_at: "2026-09-21T09:45:09Z",
        authored_text_ref: { revision_digest: "authored-1" } }], next_before_epoch: null, history_limited: false },
    }};
    if (interrupted) throw error;
    return { operation_id: "query", state: "SUCCEEDED", value: {
      operation_id: "op-1", state: "SUCCEEDED", result: { thread_id: "t", request_epoch: 1, answer: "saved answer" },
    }};
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  clients.push(client);
  const options = { queryKey: ["conversation", "p", "t"], queryFn: () => readConversation("p", "t", null) };
  await client.fetchQuery(options);
  expect(client.getQueryData<ConversationPage>(options.queryKey)?.turns[0].result?.answer).toBe("saved answer");
  interrupted = true;
  return { client, options };
}

it.each([
  ["network", () => new RpcTransportError("RPC network request failed", "NETWORK")],
  ["timeout", () => new RpcTransportError("RPC timed out", "TIMEOUT")],
  ["temporary upstream failure", () => new RpcTransportError("RPC transport failed (504)", "HTTP", 504)],
] as const)("keeps a saved answer when a return-to-window refetch has a %s error", async (_label, makeError) => {
  const { client, options } = await readableAnswerThen(makeError());
  await expect(client.fetchQuery(options)).rejects.toBeInstanceOf(Error);
  const cached = client.getQueryData<ConversationPage>(options.queryKey)!;
  expect(cached.turns[0].result?.answer).toBe("saved answer");
  expect(client.getQueryState(options.queryKey)?.status).toBe("error");
});

it("replaces a saved answer with an unavailable state when access is actually denied", async () => {
  const { client, options } = await readableAnswerThen(new RpcError("resource access rejected", -32040,
    { reason_code: "RESOURCE_SCOPE_ACCESS_DENIED" }));
  const refreshed = await client.fetchQuery(options);
  expect(refreshed.turns[0].state).toBe("UNAVAILABLE");
  expect(refreshed.turns[0].result).toBeNull();
  expect(client.getQueryData<ConversationPage>(options.queryKey)?.turns[0].result).toBeNull();
});

it("does not classify malformed result data as a temporary transport error", async () => {
  const { client, options } = await readableAnswerThen(new RpcTransportError("RPC network request failed", "NETWORK"));
  const existing = client.getQueryData<ConversationPage>(options.queryKey)!;
  vi.mocked(rpc).mockImplementation(async method => ({ operation_id: "query", state: "SUCCEEDED", value:
    method === "thread/activity/list" ? { conversation: { turns: existing.turns.map(turn => turn.input), next_before_epoch: null, history_limited: false } } : undefined,
  }));
  const refreshed = await client.fetchQuery(options);
  expect(refreshed.redacted).toBe(true);
  expect(refreshed.turns).toEqual([]);
  expect(canRetainConversation(client.getQueryState(options.queryKey)?.error)).toBe(false);
});

it("keeps an earlier stored failure when a different request is current", async () => {
  const { client, options } = await readableAnswerThen(new RpcTransportError("RPC network request failed", "NETWORK"));
  const turn = client.getQueryData<ConversationPage>(options.queryKey)!.turns[0];
  const error = { message: "earlier stored failure" };
  const merged = withCurrentCheckpoint([{ ...turn, state: "FAILED", error }], {
    request: { operation_id: "op-2" }, operation_state: "SUCCEEDED",
    previous_result: { request_ref: { revision_digest: "request-1" }, operation_id: "op-1", result: turn.result! },
  });
  expect(merged[0].state).toBe("FAILED");
  expect(merged[0].error).toBe(error);
});
