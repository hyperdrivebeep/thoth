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
const fixture = vi.hoisted(() => ({ projects: [] as Record<string, unknown>[], calls: [] as { method: string; input: Record<string, unknown> }[], hosted: false }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    let value: unknown = {};
    if (method === "workspace/ready") value = { ready: true, execution_ready: true, workspace_readable: true, setup_complete: true, workspace_id: WORKSPACE,
      deployment_mode: fixture.hosted ? "HOSTED_REVIEW" : "LOCAL", model_connected: true, setup: { internet_consent: "ALLOWED" } };
    if (method === "project/list") value = { projects: fixture.projects.filter(item => input.include_archived === true || item.lifecycle !== "ARCHIVED_READ_ONLY") };
    if (method === "project/restore") {
      const target = fixture.projects.find(item => item.project_id === input.project_id)!;
      Object.assign(target, { lifecycle: "DRAFT", revision: Number(input.expected_revision) + 1 });
      value = target;
    }
    if (method === "project/permanentDelete") {
      fixture.projects = fixture.projects.filter(item => item.project_id !== input.project_id);
      value = { project_id: input.project_id, deleted: true, already_deleted: false, deleted_at: "2026-11-04T00:00:00Z", deleted_rows: 1, cleanup_pending: [] };
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
  fixture.projects = []; fixture.calls = []; fixture.hosted = false;
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
  // Synchronous act: an async act around the floating popover waits for tens of seconds in jsdom.
  act(() => { (container.querySelector('[aria-label="old project 프로젝트 메뉴"]') as HTMLButtonElement).click(); });
  // The menu is drawn in a portal outside the sidebar so the sidebar's overflow cannot clip it.
  expect(document.querySelector('[role="menuitem"]')).not.toBeNull();
  expect(container.querySelector('[role="menu"]')).toBeNull();
  act(() => { (document.querySelector('[role="menuitem"]') as HTMLButtonElement).click(); });
  expect(confirm).toHaveBeenCalledOnce();
  const message = String(confirm.mock.calls[0][0]);
  expect(message).toContain("보관하면 읽기 전용이 되고 목록에서 숨겨집니다.");
  expect(message).toContain("'보관한 프로젝트 보기'에서 되살릴 수 있습니다.");
  expect(message).not.toContain("데이터는 보관되고");
  expect(fixture.calls.some(call => call.method === "project/archive")).toBe(false);
  act(() => root!.unmount());
  root = undefined;
}, 15000); // jsdom needs a few seconds to lay out and remove the floating menu

const typeInto = async (input: HTMLInputElement, value: string) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
});
async function deleteFromArchive(name: string) {
  await act(async () => button("보관한 프로젝트 보기")!.click());
  await settle(() => container.querySelector(".archived-projects")?.textContent?.includes(name) === true);
  await act(async () => ([...container.querySelectorAll(".archived-projects button")].find(item => item.textContent === "영구 삭제") as HTMLButtonElement).click());
  await settle(() => document.querySelector(".project-delete-dialog") !== null);
  const dialog = document.querySelector(".project-delete-dialog")!;
  await typeInto(dialog.querySelector("input")!, name);
  await act(async () => ([...dialog.querySelectorAll("button")].find(item => item.textContent === "영구 삭제") as HTMLButtonElement).click());
  await settle(() => document.querySelector(".project-delete-dialog") === null);
}

it("permanently deletes an archived project by typing its name, keeps the open project and forgets the deleted one's drafts", async () => {
  fixture.projects = [project("p", "open demo", "ACTIVE", 1), project("q", "archived demo", "ARCHIVED_READ_ONLY", 4)];
  const keep = `thoth:local-workspace:v1:draft:${JSON.stringify([WORKSPACE, "p", "t"])}`;
  const forget = `thoth:local-workspace:v1:draft:${JSON.stringify([WORKSPACE, "q", "t"])}`;
  await mount(true);
  localStorage.setItem(keep, "{}"); localStorage.setItem(forget, "{}");
  await deleteFromArchive("archived demo");
  expect(fixture.calls.filter(call => call.method === "project/permanentDelete")).toEqual([{ method: "project/permanentDelete", input: { project_id: "q", confirm_name: "archived demo" } }]);
  await settle(() => container.querySelector(".archived-projects")?.textContent?.includes("보관한 프로젝트가 없습니다") === true);
  expect(container.querySelector("#live-problem")).not.toBeNull();
  expect(localStorage.getItem(forget)).toBeNull();
  expect(localStorage.getItem(keep)).toBe("{}");
}, 20000); // the open workspace and the floating dialog are slow to lay out in jsdom

it("goes back to the project list when the deleted project was the open one", async () => {
  fixture.projects = [project("p", "open demo", "ACTIVE", 1), project("r", "other demo", "ACTIVE", 1)];
  await mount(true);
  // Another window archived it; this window still has it open until the list is read again.
  Object.assign(fixture.projects[0], { lifecycle: "ARCHIVED_READ_ONLY", revision: 2 });
  await deleteFromArchive("open demo");
  await settle(() => container.querySelector(".project-home") !== null);
  expect(container.querySelector("#live-problem")).toBeNull();
  expect(container.querySelector(".project-home")!.textContent).toContain("other demo");
  expect(container.querySelector(".project-home")!.textContent).not.toContain("open demo");
}, 20000);

it("offers no permanent delete in the hosted review environment", async () => {
  fixture.hosted = true;
  fixture.projects = [project("p", "old project", "ACTIVE", 1), project("q", "archived demo", "ARCHIVED_READ_ONLY", 4)];
  await mount(false);
  await act(async () => button("보관한 프로젝트 보기")!.click());
  await settle(() => container.querySelector(".archived-projects")?.textContent?.includes("archived demo") === true);
  expect([...container.querySelectorAll(".archived-projects button")].some(item => item.textContent === "영구 삭제")).toBe(false);
  expect(button("되살리기")).toBeDefined();
});
