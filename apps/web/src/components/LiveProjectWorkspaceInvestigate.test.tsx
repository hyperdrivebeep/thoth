// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readDraft } from "../api/conversation";
import { saveWorkspaceContext } from "../api/research";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";
import demo from "./traceDemo.fixture.json";

const WORKSPACE = `workspace:${"a".repeat(32)}`;
const scope = { mode: "LOCAL" as const, workspaceId: WORKSPACE };
const RAIN = demo.phase1.items.find(item => item.title === "Detection rate in rain")!.item_id;
const FOG = demo.phase1.items.find(item => item.title === "Detection rate in fog")!.item_id;
type Origin = { subject_kind: string; subject_id: string; verdict_revision: string } | null;
const fixture = vi.hoisted(() => ({
  threads: [] as { thread_id: string; origin: Origin }[],
  resultOrigin: null as Record<string, unknown> | null,
  calls: [] as { method: string; input: Record<string, unknown> }[],
  active: "t",
}));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    let value: unknown = {};
    if (method === "workspace/ready") value = { ready: true, execution_ready: true, workspace_readable: true, setup_complete: true,
      workspace_id: `workspace:${"a".repeat(32)}`, deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "DENIED" } };
    if (method === "project/list") value = { projects: [{ project_id: "p", name: "synthetic project", overlay: "test", lifecycle: "ACTIVE", revision: 1, cutoff_at: "2026-09-26T00:00:00Z" }] };
    if (method === "thread/list") value = { threads: fixture.threads.map((item, index) => ({ project_id: "p", thread_id: item.thread_id, updated_at: `2026-09-2${index}T00:00:00Z`,
      problem: `stored ${item.thread_id}`, execution_state: "IDLE", lifecycle: "OPEN", origin: item.origin })) };
    if (method === "thread/read") value = { project_id: "p", thread_id: input.thread_id, problem: "stored work", lifecycle: "OPEN", execution_state: "IDLE",
      current_object_ids: [], operation_state: "SUCCEEDED", current_result: null };
    if (method === "thread/activity/list") value = { conversation: { turns: [{ request_epoch: 1, request_revision_digest: "1".repeat(64), operation_id: "op-stored",
      text: "synthetic question", edit_kind: "INITIAL", created_at: "2026-09-26T00:00:00Z", authored_text_ref: { revision_digest: "1".repeat(64) } }], next_before_epoch: null, history_limited: false } };
    if (method === "operation/result/read") value = { operation_id: "op-stored", state: "SUCCEEDED",
      result: { answer: "stored synthetic answer", thread_id: fixture.active, request_epoch: 1, ...(fixture.resultOrigin ? { origin: fixture.resultOrigin } : {}) }, error: null };
    if (method === "project/source/list") value = { artifacts: [] };
    if (method === "evidence/list") value = { evidence: [] };
    if (method === "model/settings/read") value = { settings_digest: null, selection: { provider: null, model: null, reasoning_effort: null },
      effective_settings: null, availability: "UNAVAILABLE", reason_code: "MODEL_CAPABILITY_UNKNOWN", model_options: [] };
    if (method === "model/credential/list") value = { accounts: [] };
    if (method === "trace/read") value = demo.phase1;
    if (method === "thread/start" || method === "thread/input") value = { contract_version: 2, project_id: "p", thread_id: String(input.thread_id ?? "t-new"),
      operation_id: "op-new", status: "ACCEPTED_RUNNING", request_epoch: 2 };
    return { value, state: method === "thread/start" || method === "thread/input" ? "RUNNING" : "SUCCEEDED", operation_id: method === "thread/start" || method === "thread/input" ? "op-new" : "op" };
  },
}));

let root: Root | undefined; let container: HTMLDivElement; let client: QueryClient;
async function settle(check: () => boolean) {
  for (let i = 0; i < 40; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
    if (check()) return;
  }
  throw new Error(`did not settle: ${container.textContent?.slice(0, 200)}`);
}
async function mount(threadId = "") {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "synthetic" }))));
  fixture.active = threadId;
  saveWorkspaceContext("p", threadId, scope);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><LiveProjectWorkspace/></QueryClientProvider>));
  await settle(() => Boolean(container.querySelector('[role="tab"][data-tab-id="trace"]')));
}
beforeEach(() => { fixture.threads = []; fixture.resultOrigin = null; fixture.calls = []; });
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals();
});

const tab = (id: string) => container.querySelector<HTMLElement>(`[role="tab"][data-tab-id="${id}"]`)!;
const button = (label: string) => [...container.querySelectorAll("button")].find(item => (item.getAttribute("aria-label") ?? item.textContent?.trim()) === label) as HTMLButtonElement | undefined;
const editor = () => container.querySelector<HTMLTextAreaElement>("#live-problem")!;
const click = async (element: Element | undefined | null) => { expect(element).toBeTruthy(); await act(async () => (element as HTMLElement).click()); };
const type = async (text: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  await act(async () => { setter.call(editor(), text); editor().dispatchEvent(new Event("input", { bubbles: true })); });
};
const submit = async () => act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
async function investigate(label: string) {
  await click(tab("trace"));
  await settle(() => Boolean(button(`${label} 원인 조사`)));
  await click(button(`${label} 원인 조사`));
  await settle(() => Boolean(editor()));
}
const sent = (method: string) => fixture.calls.filter(call => call.method === method);
const revisionOf = (id: string) => demo.phase1.verdicts.find(item => item.subject_kind === "CRITERION" && item.subject_id === id)!.revision_digest;

it("opens a new conversation for a row with none, fills the question, starts nothing, and sends the row only when the user sends", async () => {
  fixture.threads = [{ thread_id: "t-ordinary", origin: null }];
  await mount("t-ordinary");
  await investigate("Detection rate in rain");
  expect(button("첫 조사 시작")).toBeTruthy(); // a new conversation, not the ordinary thread that was open
  expect(editor().value).toContain("Detection rate in rain");
  expect(editor().value).toContain("원인 후보와 그것을 가를 시험을 찾아 주세요.");
  expect(editor().value).not.toMatch(/HOLD_|NO_RESULT|CRITERION/);
  expect(sent("thread/start")).toHaveLength(0);
  expect(sent("thread/input")).toHaveLength(0);
  const question = editor().value;
  await submit();
  await settle(() => sent("thread/start").length === 1);
  expect(sent("thread/start")[0].input).toMatchObject({ project_id: "p", contract_version: 2, problem: question,
    origin: { kind: "TRACE_VERDICT", project_id: "p", subject_kind: "CRITERION", subject_id: RAIN, verdict_revision: revisionOf(RAIN) } });
});

it("opens the existing conversation of the same row, with the question filled in for the new verdict, and sends to that thread", async () => {
  fixture.threads = [{ thread_id: "t-other", origin: null }, { thread_id: "t-rain", origin: { subject_kind: "CRITERION", subject_id: RAIN, verdict_revision: "old-revision" } }];
  await mount("t-other");
  await investigate("Detection rate in rain");
  expect(button("지시 추가")).toBeTruthy(); // an existing thread
  expect(editor().value).toContain("Detection rate in rain");
  expect(readDraft("p", "t-rain", scope)).toContain("Detection rate in rain");
  expect(sent("thread/start")).toHaveLength(0);
  await submit();
  await settle(() => sent("thread/input").length === 1);
  expect(sent("thread/input")[0].input).toMatchObject({ thread_id: "t-rain", origin: { subject_id: RAIN, verdict_revision: revisionOf(RAIN) } });
  expect(sent("thread/start")).toHaveLength(0);
});

it("gives another row its own conversation", async () => {
  fixture.threads = [{ thread_id: "t-rain", origin: { subject_kind: "CRITERION", subject_id: RAIN, verdict_revision: "r" } }];
  await mount("t-rain");
  await investigate("Detection rate in fog");
  expect(button("첫 조사 시작")).toBeTruthy();
  expect(editor().value).toContain("Detection rate in fog");
  await submit();
  await settle(() => sent("thread/start").length === 1);
  expect(sent("thread/start")[0].input).toMatchObject({ origin: { subject_id: FOG } });
});

it("keeps what the user had written and adds the question after it", async () => {
  fixture.threads = [{ thread_id: "t-rain", origin: { subject_kind: "CRITERION", subject_id: RAIN, verdict_revision: "r" } }];
  await mount("t-rain");
  await type("내가 쓰던 메모");
  await investigate("Detection rate in rain");
  expect(editor().value.startsWith("내가 쓰던 메모\n\n")).toBe(true);
  expect(editor().value).toContain("Detection rate in rain");
});

it("does not carry the row to a different question once the user has emptied the box", async () => {
  await mount();
  await investigate("Detection rate in fog");
  await type("");
  await type("전혀 다른 질문입니다");
  await submit();
  await settle(() => sent("thread/start").length === 1);
  expect(sent("thread/start")[0].input).not.toHaveProperty("origin");
});

it("a result that came from a row says so, and the link goes back to that row on the trace page", async () => {
  fixture.threads = [{ thread_id: "t-rain", origin: { subject_kind: "CRITERION", subject_id: RAIN, verdict_revision: "r" } }];
  fixture.resultOrigin = { kind: "TRACE_VERDICT", subject_kind: "CRITERION", subject_id: RAIN, subject_title: "Detection rate in rain", state: "HOLD_NO_RESULT" };
  await mount("t-rain");
  await settle(() => Boolean(container.querySelector(".trace-origin-line")));
  const line = container.querySelector(".trace-origin-line")!.textContent!;
  expect(line).toContain("이 조사는 추적표의 「Detection rate in rain」 줄에서 시작했습니다.");
  expect(line).not.toMatch(/HOLD_|CRITERION|TRACE_VERDICT/);
  await click(button("추적표에서 보기"));
  await settle(() => Boolean(container.querySelector(".trace-focus")));
  expect(container.querySelector(".trace-focus")!.getAttribute("data-trace-row")).toBe(`CRITERION:${RAIN}`);
  expect(tab("trace").getAttribute("aria-selected")).toBe("true");
});

it("shows nothing about a row for a result that did not start from one", async () => {
  fixture.threads = [{ thread_id: "t-old", origin: null }];
  await mount("t-old");
  await settle(() => Boolean(container.textContent?.includes("stored synthetic answer")));
  expect(container.querySelector(".trace-origin-line")).toBeNull();
});
