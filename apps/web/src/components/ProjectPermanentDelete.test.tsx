// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { setLocale } from "../i18n/locale";
import { answerTopLayerSelectorsWithNo } from "../test/jsdomTopLayer";
import { ArchivedProjects } from "./ArchivedProjects";

const WORKSPACE = `workspace:${"b".repeat(32)}`;
const NAME = "Archived demo";
const fixture = vi.hoisted(() => ({
  projects: [] as Record<string, unknown>[],
  calls: [] as { method: string; input: Record<string, unknown> }[],
  failure: null as null | { code: number; details: Record<string, unknown> },
  cleanup: [] as string[],
}));
vi.mock("../api/rpcClient", async importOriginal => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  return { ...actual, rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    if (method === "project/list") return { value: { projects: fixture.projects }, state: "SUCCEEDED", operation_id: "op" };
    if (method === "project/permanentDelete") {
      if (fixture.failure) throw new actual.RpcError("refused", fixture.failure.code, fixture.failure.details);
      fixture.projects = fixture.projects.filter(item => item.project_id !== input.project_id);
      return { value: { project_id: input.project_id, deleted: true, already_deleted: false, deleted_at: "2026-11-04T00:00:00Z", deleted_rows: 9, cleanup_pending: fixture.cleanup }, state: "SUCCEEDED", operation_id: "op" };
    }
    throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
  } };
});

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
const onDeleted = vi.fn();
// The row menu is a popover; see jsdomTopLayer for why jsdom needs this answer.
let restoreMatches: () => void;
beforeAll(() => { restoreMatches = answerTopLayerSelectorsWithNo(); });
afterAll(() => restoreMatches());
const archived = () => ({ project_id: "q", name: NAME, overlay: "x", lifecycle: "ARCHIVED_READ_ONLY", revision: 4, cutoff_at: "2026-09-26T00:00:00Z" });
async function settle(check: () => boolean) {
  for (let i = 0; i < 60; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
    if (check()) return;
  }
  throw new Error(`did not settle: ${document.body.textContent?.slice(0, 300)}`);
}
const buttons = (scope: ParentNode, label: string) => [...scope.querySelectorAll("button")].filter(item => item.textContent?.trim() === label) as HTMLButtonElement[];
const dialog = () => document.body.querySelector(".project-delete-dialog") as HTMLElement | null;
const nameInput = () => dialog()?.querySelector("input") as HTMLInputElement;
const confirm = () => buttons(dialog()!, "영구 삭제")[0];
const openArchivedMenu = () => act(async () => { (container.querySelector(".archived-project-row button") as HTMLButtonElement).click(); });
const archivedMenuItems = () => [...document.querySelectorAll('.archived-project-menu [role="menuitem"]')] as HTMLButtonElement[];
/** Restore and delete are in the row's menu, which is drawn outside the list. */
const chooseArchived = async (label: string) => {
  await openArchivedMenu();
  await act(async () => { archivedMenuItems().find(item => item.textContent === label)!.click(); });
};
async function typeName(value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(nameInput(), value);
    nameInput().dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function openDialog(hosted = false, show = "보관한 프로젝트 보기") {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}>
    <ArchivedProjects scopeKey={`LOCAL:${WORKSPACE}`} hosted={hosted} storageScope={{ mode: "LOCAL", workspaceId: WORKSPACE }} onDeleted={onDeleted}/>
  </QueryClientProvider>));
  await act(async () => buttons(container, show)[0].click());
  await settle(() => container.textContent?.includes(NAME) === true);
}
beforeEach(() => { setLocale("ko"); fixture.projects = [archived()]; fixture.calls = []; fixture.failure = null; fixture.cleanup = []; onDeleted.mockClear(); });
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); document.body.innerHTML = ""; vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear();
});

it("keeps the confirm button off until the exact project name is typed", async () => {
  await openDialog();
  await chooseArchived("영구 삭제");
  await settle(() => dialog() !== null);
  expect(dialog()!.textContent).toContain("복구할 수 없습니다.");
  expect(dialog()!.textContent).toContain("대화, 자료 기록, 업로드한 원본 파일");
  expect(dialog()!.textContent).toContain("다른 프로젝트와 공유하는 파일");
  expect(confirm().disabled).toBe(true);
  for (const wrong of ["archived demo", "Archived demo ", "Archived"]) {
    await typeName(wrong);
    expect(confirm().disabled, wrong).toBe(true);
  }
  await typeName(NAME);
  expect(confirm().disabled).toBe(false);
  expect(fixture.calls.some(call => call.method === "project/permanentDelete")).toBe(false);
});

it("sends the typed name, removes the project from the list and tells the caller", async () => {
  await openDialog();
  await chooseArchived("영구 삭제");
  await settle(() => dialog() !== null);
  await typeName(NAME);
  await act(async () => confirm().click());
  await settle(() => dialog() === null);
  expect(fixture.calls.filter(call => call.method === "project/permanentDelete").map(call => call.input)).toEqual([{ project_id: "q", confirm_name: NAME }]);
  expect(onDeleted).toHaveBeenCalledWith("q");
  await settle(() => container.textContent?.includes("보관한 프로젝트가 없습니다") === true);
  expect(container.textContent).toContain("프로젝트를 영구 삭제했습니다.");
});

it("forgets the drafts and last place this browser kept for the deleted project only", async () => {
  const draft = (project: string) => `thoth:local-workspace:v1:draft:${JSON.stringify([WORKSPACE, project, "t"])}`;
  const pending = `thoth:local-workspace:v1:pending:${JSON.stringify([WORKSPACE, "q", "t"])}`;
  localStorage.setItem(draft("q"), "{}"); localStorage.setItem(draft("other"), "{}"); localStorage.setItem(pending, "{}");
  localStorage.setItem(`thoth:local-workspace:v1:context:${WORKSPACE}`, JSON.stringify({ version: 1, workspaceId: WORKSPACE, projectId: "q", threadId: "t" }));
  sessionStorage.setItem(`thoth:draft:v1:${JSON.stringify(["q", "t"])}`, "unsent");
  await openDialog();
  await chooseArchived("영구 삭제");
  await settle(() => dialog() !== null);
  await typeName(NAME);
  await act(async () => confirm().click());
  await settle(() => dialog() === null);
  expect(localStorage.getItem(draft("q"))).toBeNull();
  expect(localStorage.getItem(pending)).toBeNull();
  expect(localStorage.getItem(draft("other"))).toBe("{}");
  expect(JSON.parse(localStorage.getItem(`thoth:local-workspace:v1:context:${WORKSPACE}`)!)).toMatchObject({ projectId: "", threadId: "" });
  expect(sessionStorage.length).toBe(0);
});

it.each([
  [-32030, { reason_code: "PROJECT_NOT_ARCHIVED" }, "보관한 프로젝트만 영구 삭제할 수 있습니다.", "Only an archived project can be deleted permanently."],
  [-32030, { reason_code: "PROJECT_NAME_MISMATCH" }, "프로젝트 이름이 일치하지 않습니다.", "The project name does not match."],
  [-32030, { reason_code: "PROJECT_REFERENCED_BY_OTHER_PROJECTS", referencing_project_ids: ["project:beta", "project:gamma"] },
    "참조하는 프로젝트: project:beta, project:gamma", "Referencing projects: project:beta, project:gamma"],
  [-32030, { reason_code: "PROJECT_HAS_ACTIVE_OPERATION" }, "작업이 아직 실행 중입니다", "Work in this project is still running."],
  [-32040, { reason_code: "PROJECT_DELETE_LOCAL_ONLY" }, "호스팅 환경에서는 프로젝트를 영구 삭제할 수 없습니다", "cannot be deleted permanently in the hosted review environment"],
  [-32040, { reason_code: "PROJECT_DELETE_SCOPE_DENIED" }, "삭제할 권한이 없습니다", "do not have permission"],
  [-32020, {}, "프로젝트를 찾을 수 없습니다", "The project was not found."],
  [-32030, { reason_code: "PROJECT_PURGE_TABLE_UNCLASSIFIED:later_added" }, "프로젝트를 영구 삭제하지 못했습니다", "could not be deleted permanently"],
])("shows a readable reason when the server refuses (%s %j)", async (code, details, korean, english) => {
  fixture.failure = { code, details };
  await openDialog();
  await chooseArchived("영구 삭제");
  await settle(() => dialog() !== null);
  await typeName(NAME);
  await act(async () => confirm().click());
  await settle(() => dialog()?.querySelector('[role="alert"]') !== null);
  expect(dialog()!.querySelector('[role="alert"]')!.textContent).toContain(korean);
  expect(onDeleted).not.toHaveBeenCalled();
  expect(confirm().disabled).toBe(false);
  await act(async () => setLocale("en"));
  expect(dialog()!.querySelector('[role="alert"]')!.textContent).toContain(english);
});

it("keeps the dialog open when files are left, and trying again finishes the same request", async () => {
  fixture.cleanup = ["inbox/web/projects/abc"];
  await openDialog();
  await chooseArchived("영구 삭제");
  await settle(() => dialog() !== null);
  await typeName(NAME);
  await act(async () => confirm().click());
  await settle(() => dialog()?.textContent?.includes("일부 파일을 지우지 못했습니다. 다시 시도하면 마저 지웁니다.") === true);
  expect(onDeleted).toHaveBeenCalledWith("q");
  expect(buttons(dialog()!, "다시 시도")).toHaveLength(1);
  fixture.cleanup = [];
  await act(async () => buttons(dialog()!, "다시 시도")[0].click());
  await settle(() => dialog() === null);
  expect(fixture.calls.filter(call => call.method === "project/permanentDelete").map(call => call.input)).toEqual([
    { project_id: "q", confirm_name: NAME }, { project_id: "q", confirm_name: NAME },
  ]);
});

it("offers no delete button in the hosted review environment", async () => {
  await openDialog(true);
  await openArchivedMenu();
  expect(archivedMenuItems().map(item => item.textContent)).toEqual(["되살리기"]);
});

it("shows the delete wording in English too", async () => {
  setLocale("en");
  await openDialog(false, "View archived projects");
  await chooseArchived("Delete permanently");
  await settle(() => dialog() !== null);
  expect(dialog()!.textContent).toContain("This cannot be undone.");
  expect(dialog()!.textContent).toContain('Delete project "Archived demo" permanently');
  expect(buttons(dialog()!, "Cancel")).toHaveLength(1);
});

it("shows the delete wording in Simplified Chinese too", async () => {
  setLocale("zh-Hans");
  await openDialog(false, "查看已归档的项目");
  await chooseArchived("永久删除");
  await settle(() => dialog() !== null);
  expect(dialog()!.textContent).toContain("此操作无法撤销。");
  expect(dialog()!.textContent).toContain("永久删除项目“Archived demo”");
  expect(dialog()!.textContent).not.toMatch(/[가-힣]/);
  expect(buttons(dialog()!, "取消")).toHaveLength(1);
  expect(buttons(dialog()!, "永久删除")[0].disabled).toBe(true);
  await typeName(NAME);
  expect(buttons(dialog()!, "永久删除")[0].disabled).toBe(false);
});

it("shows the delete wording in Japanese too", async () => {
  setLocale("ja");
  await openDialog(false, "アーカイブ済みのプロジェクトを見る");
  await chooseArchived("完全に削除");
  await settle(() => dialog() !== null);
  expect(dialog()!.textContent).toContain("この操作は元に戻せません。");
  expect(dialog()!.textContent).toContain("プロジェクト「Archived demo」を完全に削除");
  expect(dialog()!.textContent).not.toMatch(/[가-힣]/);
  expect(buttons(dialog()!, "キャンセル")).toHaveLength(1);
  expect(buttons(dialog()!, "完全に削除")[0].disabled).toBe(true);
  await typeName(NAME);
  expect(buttons(dialog()!, "完全に削除")[0].disabled).toBe(false);
});
