// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { saveWorkspaceContext } from "../api/research";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

// A LOCAL workspace with one project, two connected sources, one saved session and no model connected.
const WORKSPACE = `workspace:${"b".repeat(32)}`;
const fixture = vi.hoisted(() => ({ modelConnected: false, consent: "DENIED", calls: [] as string[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push(method);
    let value: unknown = {};
    if (method === "workspace/setup/read") value = { internet_consent: fixture.consent, internet_grant_id: null };
    if (method === "workspace/ready") value = { ready: fixture.modelConnected, execution_ready: fixture.modelConnected, workspace_readable: true, setup_complete: true,
      workspace_id: WORKSPACE, deployment_mode: "LOCAL", model_connected: fixture.modelConnected, setup: { internet_consent: fixture.consent } };
    if (method === "project/list") value = { projects: [{ project_id: "p", name: "flow project", overlay: "test", lifecycle: "ACTIVE", revision: 1, cutoff_at: "2026-11-04T12:18:00Z" }] };
    if (method === "thread/list") value = { threads: [{ project_id: input.project_id, thread_id: "t", updated_at: "2026-09-20T00:00:00Z", problem: "stored work", execution_state: "IDLE", lifecycle: "OPEN" }] };
    if (method === "thread/read") value = { project_id: input.project_id, thread_id: input.thread_id, problem: "stored work", lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [], operation_state: "SUCCEEDED", current_result: null };
    if (method === "thread/activity/list") value = { conversation: { turns: [], next_before_epoch: null, history_limited: false } };
    if (method === "project/source/list") value = { artifacts: [{ artifact_id: "a1", source_uri: "file:///flow-a.md", media_type: "text/markdown" }, { artifact_id: "a2", source_uri: "file:///flow-b.txt", media_type: "text/plain" }] };
    if (method === "evidence/list") value = { evidence: [] };
    if (method === "model/settings/read") value = { settings_digest: null, selection: { provider: null, model: null, reasoning_effort: null }, effective_settings: null, availability: "UNAVAILABLE", reason_code: "MODEL_CAPABILITY_UNKNOWN", model_options: [] };
    if (method === "model/credential/list") value = { accounts: [] };
    return { value, state: "SUCCEEDED", operation_id: "op" };
  },
}));

let root: Root | undefined; let container: HTMLDivElement; let client: QueryClient;
async function settle(check: () => boolean) {
  for (let i = 0; i < 40; i++) { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); if (check()) return; }
  throw new Error(`did not settle: ${container.textContent?.slice(0, 300)}`);
}
const buttons = () => [...container.querySelectorAll<HTMLButtonElement>("button")];
async function mount(threadId: string) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "synthetic" }))));
  saveWorkspaceContext("p", threadId, { mode: "LOCAL", workspaceId: WORKSPACE });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><LiveProjectWorkspace /></QueryClientProvider>));
  await settle(() => Boolean(container.querySelector("#live-problem")));
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); fixture.calls = [];
});

it("shows the readiness card on a fresh conversation from values the shell already reads, and says the blocker once", async () => {
  await mount("");
  await settle(() => Boolean(container.querySelector(".project-readiness")));
  const card = container.querySelector(".project-readiness")!;
  expect(card.textContent).toContain("자료 2건 연결됨");
  expect(card.textContent).toContain("모델 미연결");
  expect(card.textContent).toContain("인터넷 사용 안 함");
  expect(card.textContent).toContain("기준시점");
  expect(card.textContent).toContain("새 연구를 실행하려면 모델 연결을 확인하세요");
  // the composer no longer repeats the sentence under the card
  expect((container.textContent!.match(/새 연구를 실행하려면 모델 연결을 확인하세요/g) ?? []).length).toBe(1);
  expect(container.querySelector(".dot")).toBeNull();
  // one sources read for both the page and the count chips
  expect(fixture.calls.filter(method => method === "project/source/list")).toHaveLength(1);
  const counts = container.querySelector(".workspace-counts")!;
  expect(counts.textContent).toContain("대화 1");
  expect(counts.textContent).toContain("자료 2");
  // the top bar has no select for the conversation; conversations are chosen in the sidebar
  expect(container.querySelector(".workstation-topbar select")).toBeNull();
  expect(container.querySelectorAll(".project-sessions .thread-button")).toHaveLength(1);
  // the card's action opens this computer's settings, where the model accounts are
  await act(async () => buttons().find(item => item.textContent === "모델 연결 확인")!.click());
  await settle(() => container.querySelector(".workspace-heading h1")?.textContent === "이 컴퓨터 설정");
  expect(container.textContent).toContain("모델 연결");
  expect(container.textContent).not.toContain("모델 기본값");
});

it("makes the setup-needed status in the header a button that opens this computer's settings", async () => {
  await mount("t");
  const setup = buttons().find(item => item.textContent?.includes("새 연구 준비 필요"))!;
  expect(setup).toBeDefined();
  expect(setup.getAttribute("title")).toBe("새 연구를 실행하려면 설정이 필요합니다. 누르면 설정으로 이동합니다.");
  // a stored session keeps the composer's own notice (no readiness card there)
  expect(container.querySelector(".project-readiness")).toBeNull();
  expect(container.textContent).toContain("새 연구를 실행하려면 모델 연결을 확인하세요");
  await act(async () => setup.click());
  await settle(() => container.querySelector(".workspace-heading h1")?.textContent === "이 컴퓨터 설정");
  // It is not one of the project's tabs: the project's own "설정" tab is a different screen.
  expect([...container.querySelectorAll('[role="tab"]')].filter(tab => tab.getAttribute("aria-selected") === "true")).toHaveLength(0);
});

it("says once on the project's settings that no model is connected, instead of two switched-off model choices", async () => {
  await mount("t");
  await act(async () => ([...container.querySelectorAll('[role="tab"]')].find(tab => tab.textContent === "설정") as HTMLElement).click());
  await settle(() => container.querySelector(".workspace-heading h1")?.textContent === "프로젝트 설정");
  const card = [...container.querySelectorAll(".workspace-content .detail-card")].find(item => item.querySelector("h2")?.textContent === "모델 기본값")!;
  expect(card.textContent).toContain("연결된 모델이 없습니다. 모델을 연결하면 여기서 기본 모델을 고를 수 있습니다.");
  expect(card.querySelector("fieldset.model-settings")).toBeNull();
  expect((container.textContent!.match(/연결된 모델이 없습니다/g) ?? []).length).toBe(1);
  await act(async () => ([...card.querySelectorAll("button")].find(item => item.textContent === "이 컴퓨터 설정 열기") as HTMLButtonElement).click());
  await settle(() => container.querySelector(".workspace-heading h1")?.textContent === "이 컴퓨터 설정");
});
