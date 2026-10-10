// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { writeDraft } from "../api/conversation";
import { saveWorkspaceContext } from "../api/research";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

// A LOCAL workspace that already has a project, with the internet consent still empty (made by an older version or by a script).
const WORKSPACE = `workspace:${"a".repeat(32)}`;
const fixture = vi.hoisted(() => ({ consent: "UNDECIDED", modelConnected: true, calls: [] as { method: string; input: Record<string, unknown> }[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    let value: unknown = {};
    if (method === "workspace/setup/update") fixture.consent = String(input.internet_consent);
    if (method === "workspace/setup/read") value = { internet_consent: fixture.consent, internet_grant_id: null };
    if (method === "workspace/ready") {
      const decided = fixture.consent !== "UNDECIDED";
      value = { ready: decided && fixture.modelConnected, execution_ready: decided && fixture.modelConnected, workspace_readable: true,
        setup_complete: decided, workspace_id: WORKSPACE, deployment_mode: "LOCAL", model_connected: fixture.modelConnected,
        setup: { internet_consent: fixture.consent } };
    }
    if (method === "project/list") value = { projects: [{ project_id: "p", name: "old project", overlay: "test", lifecycle: "ACTIVE", revision: 1, cutoff_at: "2026-09-26T00:00:00Z" }] };
    if (method === "thread/list") value = { threads: [{ project_id: input.project_id, thread_id: "t", updated_at: "2026-09-20T00:00:00Z", problem: "stored work", execution_state: "IDLE", lifecycle: "OPEN" }] };
    if (method === "thread/read") value = { project_id: input.project_id, thread_id: input.thread_id, problem: "stored work", lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [], operation_state: "SUCCEEDED", current_result: null };
    if (method === "thread/activity/list") value = { conversation: { turns: [], next_before_epoch: null, history_limited: false } };
    if (method === "project/source/list") value = { artifacts: [] };
    if (method === "evidence/list") value = { evidence: [] };
    if (method === "model/settings/read") value = { settings_digest: null, selection: { provider: null, model: null, reasoning_effort: null }, effective_settings: null, availability: "UNAVAILABLE", reason_code: "MODEL_CAPABILITY_UNKNOWN", model_options: [] };
    if (method === "model/credential/list") value = { accounts: [] };
    return { value, state: "SUCCEEDED", operation_id: "op" };
  },
}));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
async function settle(check: () => boolean) {
  for (let i = 0; i < 30; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
    if (check()) return;
  }
  throw new Error(`did not settle: ${container.textContent?.slice(0, 260)}`);
}
const button = (label: string) => [...container.querySelectorAll("button")].find(item => item.textContent === label) as HTMLButtonElement | undefined;
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "synthetic" }))));
  saveWorkspaceContext("p", "t", { mode: "LOCAL", workspaceId: WORKSPACE });
  writeDraft("p", "t", "a question to send", { mode: "LOCAL", workspaceId: WORKSPACE });
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><LiveProjectWorkspace /></QueryClientProvider>));
  await settle(() => Boolean(container.querySelector("#live-problem")));
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals();
  fixture.consent = "UNDECIDED"; fixture.modelConnected = true; fixture.calls = [];
});

it("names the empty internet consent as the reason, and the settings page lets the user choose it", async () => {
  await mount();
  const submit = () => container.querySelector('[aria-label="지시 추가"]') as HTMLButtonElement;
  expect(container.textContent).toContain("인터넷 사용 동의를 고르세요");
  expect(container.textContent).not.toContain("모델 연결과 초기 설정을 확인하세요");
  expect(submit().disabled).toBe(true);
  await act(async () => button("동의 고르기")!.click());
  await settle(() => Boolean(container.querySelector("#workspace-internet-consent")));
  const card = container.querySelector("#workspace-internet-consent") as HTMLElement;
  expect(card.textContent).toContain("공개 웹 자료 조회");
  expect(card.textContent).toContain("아직 고르지 않았습니다");
  expect(fixture.calls.some(call => call.method === "workspace/setup/update")).toBe(false);
  await act(async () => button("허용")!.click());
  await settle(() => card.textContent?.includes("허용함") === true);
  const update = fixture.calls.filter(call => call.method === "workspace/setup/update");
  expect(update).toEqual([{ method: "workspace/setup/update", input: { internet_consent: "ALLOWED" } }]);
  await act(async () => ([...container.querySelectorAll('[role="tab"]')].find(tab => tab.textContent === "대화") as HTMLElement).click());
  await settle(() => submit() !== null && !container.textContent?.includes("인터넷 사용 동의를 고르세요"));
  await settle(() => !submit().disabled);
  expect(container.textContent).not.toContain("새 연구를 실행하려면");
});

it("lets the user deny it from the settings page too, and keeps both choices visible", async () => {
  await mount();
  await act(async () => button("동의 고르기")!.click());
  await settle(() => Boolean(container.querySelector("#workspace-internet-consent")));
  const card = container.querySelector("#workspace-internet-consent") as HTMLElement;
  expect([...card.querySelectorAll("button")].map(item => item.textContent)).toEqual(["허용", "거부"]);
  await act(async () => button("거부")!.click());
  await settle(() => card.textContent?.includes("거부함") === true);
  expect(fixture.calls.filter(call => call.method === "workspace/setup/update")[0].input).toEqual({ internet_consent: "DENIED" });
  expect([...card.querySelectorAll("button")].map(item => item.textContent)).toEqual(["허용", "거부"]);
});

it("says both when the model is not connected either, and keeps the old words when only the model is missing", async () => {
  fixture.modelConnected = false;
  await mount();
  expect(container.textContent).toContain("모델 연결과 인터넷 사용 동의를 확인하세요");
  await act(async () => root!.unmount());
  container.remove(); client.clear(); localStorage.clear(); sessionStorage.clear();
  fixture.consent = "DENIED";
  await mount();
  expect(container.textContent).toContain("새 연구를 실행하려면 모델 연결을 확인하세요");
  expect(container.textContent).not.toContain("인터넷 사용 동의를 고르세요");
  expect(button("모델 연결 확인")).toBeDefined();
});
