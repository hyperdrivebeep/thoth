// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { rpc } from "../api/rpcClient";
import type { TraceItem, TraceView } from "../api/trace";
import { ConversationTimeline } from "./ConversationTimeline";

vi.mock("../api/rpcClient", async importOriginal => ({
  ...await importOriginal<typeof import("../api/rpcClient")>(),
  rpc: vi.fn(),
}));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;

const item = (title: string, fields: Record<string, string> = {}): TraceItem => ({ item_id: "i1", item_key: "k1", kind: "REQUIREMENT", title, fields, source_span_refs: [] });
const view = (...items: TraceItem[]): TraceView => ({ record_digest: null, set_digest: "d", items, links: [], rules: [], results: [], verdicts: [], confirmations: [], pending_changes: [] });
const conversation = { operation_id: "", state: "SUCCEEDED", value: { conversation: { turns: [], next_before_epoch: null, history_limited: false } } };

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); vi.resetAllMocks();
});

async function mount(trace: TraceView | Error) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockImplementation(async method => {
    if (method === "trace/read") { if (trace instanceof Error) throw trace; return { operation_id: "", state: "SUCCEEDED", value: trace }; }
    return conversation;
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root!.render(<QueryClientProvider client={client}>
    <ConversationTimeline projectId="p" threadId="t" onDetail={() => undefined}/></QueryClientProvider>));
  for (let index = 0; index < 30; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    if (container.textContent?.includes("이 작업의 대화 기록을 현재 화면에서 불러올 수 없습니다")) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); }); // let the trace read settle too
      return;
    }
  }
  throw new Error("The conversation never finished loading");
}
const notice = () => container.querySelector(".conversation-synthetic-notice");

it("shows a thin synthetic-data line above the conversation when the project's trace carries the demo marker", async () => {
  await mount(view(item("SYNTHETIC DEMO DATA - 요구사항")));
  expect(notice()?.textContent).toBe("합성 데이터입니다. 실제 측정값이 아니며 공학 용도로 쓸 수 없습니다.");
  expect(container.firstElementChild!.firstElementChild).toBe(notice());
  const calls = vi.mocked(rpc).mock.calls.map(([method]) => method);
  expect(calls.filter(method => method === "trace/read")).toHaveLength(1);
});

it("also recognises the synthetic_notice field the trace banner reads", async () => {
  await mount(view(item("요구사항", { synthetic_notice: "SYNTHETIC DEMO DATA - NOT MEASURED" })));
  expect(notice()).not.toBeNull();
});

it("shows nothing for an ordinary project", async () => {
  await mount(view(item("검출율 요구사항", { note: "실측" })));
  expect(notice()).toBeNull();
  expect(container.textContent).not.toContain("합성 데이터");
});

it("shows nothing when the trace cannot be read or has no trace table", async () => {
  await mount(new Error("denied"));
  expect(notice()).toBeNull();
  await act(async () => root!.unmount()); root = undefined; client.clear(); container.remove();
  await mount(view());
  expect(notice()).toBeNull();
});
