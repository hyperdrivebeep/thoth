// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationTimeline } from "./ConversationTimeline";
import { rpc } from "../api/rpcClient";
import type { ResearchStatus } from "../api/research";
import { setLocale, t } from "../i18n/locale";

vi.mock("../api/rpcClient", async importOriginal => ({ ...await importOriginal<typeof import("../api/rpcClient")>(), rpc: vi.fn() }));

const input = { request_epoch: 1, request_revision_digest: "request-1", operation_id: "op-1", text: "question-1", edit_kind: "APPEND",
  created_at: "2026-11-05T09:45:09Z", authored_text_ref: { revision_digest: "authored-1" } };
const status = { project_id: "p", thread_id: "t", cycle_id: "c", problem: "q", lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [],
  working_head_digest: "h", operation_state: "SUCCEEDED", request: { operation_id: "op-1", authored_text: "question-1" } } as ResearchStatus;

afterEach(() => { setLocale("ko"); vi.resetAllMocks(); });

/** A saved turn that ended on a cut-off model call (or on another reason), with the settings read answering as given. */
async function show(reason: string, settings: "off" | "on" | "error"): Promise<{ text: string; click: () => Promise<void>; opened: ReturnType<typeof vi.fn>; stop: () => Promise<void> }> {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockImplementation(async method => {
    if (method === "thread/activity/list") return { operation_id: "read", state: "SUCCEEDED", value: { conversation: { turns: [input], next_before_epoch: null, history_limited: false } } };
    if (method === "operation/result/read") return { operation_id: "read", state: "SUCCEEDED", value: { operation_id: "op-1", state: "SUCCEEDED", result: { thread_id: "t", request_epoch: 1, terminal_reason: reason } } };
    if (method === "model/callSettings/read") {
      if (settings === "error") throw new Error("denied");
      return { operation_id: "read", state: "SUCCEEDED", value: { auto_retry_interrupted_model_call: settings === "on", settings_digest: "d" } };
    }
    return { operation_id: "read", state: "SUCCEEDED", value: {} };
  });
  const opened = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const node = document.createElement("div"); document.body.append(node);
  const root = createRoot(node);
  await act(async () => root.render(<QueryClientProvider client={client}><ConversationTimeline projectId="p" threadId="t" status={status} onDetail={() => undefined} onOpenSettings={opened}/></QueryClientProvider>));
  for (let i = 0; i < 50 && !node.querySelector(".assistant-message"); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
  return {
    text: node.textContent ?? "", opened,
    click: async () => { await act(async () => [...node.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes(t("result.autoRetryOpenSettings")))!.click()); },
    stop: async () => { await act(async () => root.unmount()); node.remove(); client.clear(); },
  };
}

it("shows that automatic retry is off on a cut-off result, and the button opens the settings", async () => {
  const view = await show("OAUTH_TRANSPORT_FAILURE", "off");
  expect(view.text).toContain("끊긴 모델 호출 자동 다시 시도가 꺼져 있습니다.");
  await view.click();
  expect(view.opened).toHaveBeenCalledOnce();
  expect(vi.mocked(rpc).mock.calls.filter(call => call[0] === "model/callSettings/read")).toHaveLength(1);
  await view.stop();
});

it("shows nothing when automatic retry is on, when the setting cannot be read, or when the run was not cut off by a model call", async () => {
  for (const [reason, settings] of [["OAUTH_TRANSPORT_FAILURE", "on"], ["OAUTH_TRANSPORT_FAILURE", "error"], ["TOTAL_RESEARCH_BUDGET_EXHAUSTED", "off"]] as const) {
    const view = await show(reason, settings);
    expect(view.text, reason + settings).toContain(t("result.incomplete"));
    expect(view.text, reason + settings).not.toContain(t("result.autoRetryOff"));
    await view.stop();
    vi.resetAllMocks();
  }
});

