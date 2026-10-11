// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { saveWorkspaceContext } from "../api/research";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

const WORKSPACE = `workspace:${"a".repeat(32)}`;
const project = (id: string, name: string, lifecycle: string, revision: number) =>
  ({ project_id: id, name, overlay: "test", lifecycle, revision, cutoff_at: "2026-09-26T00:00:00Z" });
const fixture = vi.hoisted(() => ({ projects: [] as Record<string, unknown>[], calls: [] as { method: string; input: Record<string, unknown> }[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    let value: unknown = {};
    if (method === "workspace/ready") value = { ready: true, execution_ready: true, workspace_readable: true, setup_complete: true, workspace_id: WORKSPACE,
      deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "ALLOWED" } };
    if (method === "project/list") value = { projects: fixture.projects.filter(item => input.include_archived === true || item.lifecycle !== "ARCHIVED_READ_ONLY") };
    if (method === "project/restore") {
      const target = fixture.projects.find(item => item.project_id === input.project_id)!;
      Object.assign(target, { lifecycle: "DRAFT", revision: Number(input.expected_revision) + 1 });
      value = target;
    }
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
  for (let i = 0; i < 40; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
    if (check()) return;
  }
  throw new Error(`did not settle: ${container.textContent?.slice(0, 260)}`);
}
const button = (label: string) => [...container.querySelectorAll("button")].find(item => item.textContent === label) as HTMLButtonElement | undefined;
async function mount(selected: boolean) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "synthetic" }))));
  if (selected) saveWorkspaceContext("p", "t", { mode: "LOCAL", workspaceId: WORKSPACE });
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><LiveProjectWorkspace /></QueryClientProvider>));
  await settle(() => Boolean(container.querySelector(selected ? "#live-problem" : ".project-home")));
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals();
  fixture.projects = []; fixture.calls = [];
});

const newProject = () => container.querySelector("button.new-project-button") as HTMLButtonElement;
const nameInput = () => [...container.querySelectorAll("label")].find(item => item.textContent?.startsWith("프로젝트 이름"));

it("opens the create screen from the left button on the first screen, where it used to do nothing", async () => {
  fixture.projects = [project("p", "old project", "ACTIVE", 1)];
  await mount(false);
  expect(nameInput()).toBeUndefined();
  await act(async () => newProject().click());
  await settle(() => nameInput() !== undefined);
  // pressing it again after going back to the list opens it again
  await act(async () => button("프로젝트 선택으로 돌아가기")!.click());
  await settle(() => nameInput() === undefined);
  await act(async () => newProject().click());
  await settle(() => nameInput() !== undefined);
});

it("opens the create screen from the left button while a project is open", async () => {
  fixture.projects = [project("p", "old project", "ACTIVE", 1)];
  await mount(true);
  expect(nameInput()).toBeUndefined();
  await act(async () => newProject().click());
  await settle(() => nameInput() !== undefined);
  expect(container.querySelector("#live-problem")).toBeNull();
});

it("lists archived projects on demand and restores one, then shows it in the normal list", async () => {
  fixture.projects = [project("p", "old project", "ACTIVE", 1), project("q", "archived demo", "ARCHIVED_READ_ONLY", 4)];
  await mount(false);
  expect(container.textContent).not.toContain("archived demo");
  expect(fixture.calls.filter(call => call.method === "project/list").every(call => call.input.include_archived !== true)).toBe(true);
  await act(async () => button("보관한 프로젝트 보기")!.click());
  await settle(() => container.textContent?.includes("archived demo") === true);
  expect(fixture.calls.some(call => call.method === "project/list" && call.input.include_archived === true)).toBe(true);
  expect(container.querySelector(".archived-projects")!.textContent).not.toContain("old project");
  await act(async () => button("되살리기")!.click());
  await settle(() => container.querySelector(".archived-projects")?.textContent?.includes("보관한 프로젝트가 없습니다") === true);
  expect(fixture.calls.filter(call => call.method === "project/restore")).toEqual([{ method: "project/restore", input: { project_id: "q", expected_revision: 4 } }]);
  await settle(() => container.querySelector(".navigation-rail")?.textContent?.includes("archived demo") === true);
});

it("tells the user that archiving makes a project read-only and that it can be restored", async () => {
  fixture.projects = [project("p", "old project", "ACTIVE", 1)];
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await mount(false);
  await act(async () => (container.querySelector('[aria-label="old project 프로젝트 메뉴"]') as HTMLButtonElement).click());
  await act(async () => button("프로젝트 제거")!.click());
  expect(confirm).toHaveBeenCalledOnce();
  const message = String(confirm.mock.calls[0][0]);
  expect(message).toContain("보관하면 읽기 전용이 되고 목록에서 숨겨집니다.");
  expect(message).toContain("'보관한 프로젝트 보기'에서 되살릴 수 있습니다.");
  expect(message).not.toContain("데이터는 보관되고");
  expect(fixture.calls.some(call => call.method === "project/archive")).toBe(false);
});
