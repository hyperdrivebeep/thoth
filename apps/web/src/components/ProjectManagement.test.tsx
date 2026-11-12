// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { saveWorkspaceContext } from "../api/research";
import { answerTopLayerSelectorsWithNo } from "../test/jsdomTopLayer";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

const WORKSPACE = `workspace:${"a".repeat(32)}`;
type Row = Record<string, unknown>;
const fixture = vi.hoisted(() => ({
  projects: [] as Record<string, unknown>[],
  threads: [] as Record<string, unknown>[],
  artifacts: [] as Record<string, unknown>[],
  bindings: [] as Record<string, unknown>[],
  calls: [] as { method: string; input: Record<string, unknown> }[],
}));
vi.mock("../api/rpcClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  const stale = (current: unknown, expected: unknown) => {
    if (current !== expected)
      throw new actual.RpcError("revision does not match", -32030, {
        current_revision: current,
        expected_revision: expected,
      });
  };
  return {
    ...actual,
    rpc: async (method: string, input: Record<string, unknown>) => {
      fixture.calls.push({ method, input });
      let value: unknown = {};
      const project = fixture.projects.find((item) => item.project_id === input.project_id);
      if (method === "workspace/ready") {
        value = {
          ready: true,
          execution_ready: true,
          workspace_readable: true,
          setup_complete: true,
          workspace_id: WORKSPACE,
          deployment_mode: "LOCAL",
          model_connected: true,
          setup: { internet_consent: "ALLOWED" },
        };
      }
      if (method === "project/list")
        value = { projects: fixture.projects.map((item) => ({ ...item })) };
      if (method === "project/metadata/update") {
        stale(project!.revision, input.expected_revision);
        Object.assign(project!, { name: input.name, revision: Number(project!.revision) + 1 });
        value = project;
      }
      if (method === "thread/list")
        value = { threads: fixture.threads.map((item) => ({ ...item })) };
      if (method === "thread/metadata/update") {
        const thread = fixture.threads.find((item) => item.thread_id === input.thread_id)!;
        stale(thread.revision, input.expected_revision);
        Object.assign(thread, {
          display_name: input.display_name,
          revision: Number(thread.revision) + 1,
        });
        value = thread;
      }
      if (method === "thread/read") {
        const thread = fixture.threads.find((item) => item.thread_id === input.thread_id)!;
        value = {
          ...thread,
          current_object_ids: [],
          operation_state: "SUCCEEDED",
          current_result: null,
        };
      }
      if (method === "thread/activity/list")
        value = { conversation: { turns: [], next_before_epoch: null, history_limited: false } };
      if (method === "project/source/list") {
        value = {
          artifacts: fixture.artifacts.map((item) => ({ ...item })),
          bindings: fixture.bindings.map((item) => ({ ...item })),
          source_times: [],
          cutoff_basis: { cutoff_at: "2026-09-26T00:00:00Z", project_revision: project?.revision },
        };
      }
      if (method === "project/source/disconnect") {
        stale(project!.revision, input.expected_project_revision);
        const binding = fixture.bindings.find((item) => item.binding_id === input.binding_id)!;
        binding.state = "DETACHED";
        project!.revision = Number(project!.revision) + 1;
        value = { binding };
      }
      if (method === "evidence/list") value = { evidence: [] };
      if (method === "model/settings/read") {
        value = {
          settings_digest: null,
          selection: { provider: null, model: null, reasoning_effort: null },
          effective_settings: null,
          availability: "UNAVAILABLE",
          reason_code: "MODEL_CAPABILITY_UNKNOWN",
          model_options: [],
        };
      }
      if (method === "model/credential/list") value = { accounts: [] };
      return { value, state: "SUCCEEDED", operation_id: "op" };
    },
  };
});

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
let restoreMatches: () => void;
beforeAll(() => {
  restoreMatches = answerTopLayerSelectorsWithNo();
});
afterAll(() => restoreMatches());

async function settle(check: () => boolean) {
  for (let i = 0; i < 40; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 15));
    });
    if (check()) return;
  }
  throw new Error("did not settle: " + container.textContent?.slice(0, 260));
}
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "synthetic" }))),
  );
  saveWorkspaceContext("p", "t", { mode: "LOCAL", workspaceId: WORKSPACE });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () =>
    root!.render(
      <QueryClientProvider client={client}>
        <LiveProjectWorkspace />
      </QueryClientProvider>,
    ),
  );
  await settle(() => Boolean(container.querySelector("#live-problem")));
}
const seed = () => {
  fixture.projects = [
    {
      project_id: "p",
      name: "레이더 지연",
      overlay: "test",
      lifecycle: "ACTIVE",
      revision: 3,
      cutoff_at: "2026-09-26T00:00:00Z",
    },
  ];
  fixture.threads = [
    {
      project_id: "p",
      thread_id: "t",
      updated_at: "2026-09-20T00:00:00Z",
      problem: "LAB-42의 지연 원인은?",
      display_name: "LAB-42의 지연 원인은?",
      revision: 4,
      execution_state: "IDLE",
      lifecycle: "OPEN",
    },
  ];
  const artifact = (id: string, name: string): Row => ({
    artifact_id: id,
    project_id: "p",
    source_uri: "file:///" + name,
    media_type: "application/pdf",
    byte_sha256: "0".repeat(64),
    authority_state: "OFFICIAL",
    security_class: "INTERNAL",
    extraction_coverage: "FULL",
    cutoff_state: "ON_OR_BEFORE_CUTOFF",
  });
  fixture.artifacts = [
    artifact("artifact:a", "radar-log.pdf"),
    artifact("artifact:b", "weather.pdf"),
  ];
  fixture.bindings = [
    { binding_id: "binding:a", artifact_id: "artifact:a", state: "ACTIVE" },
    { binding_id: "binding:b", artifact_id: "artifact:b", state: "ACTIVE" },
  ];
};
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  client?.clear();
  container?.remove();
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  fixture.projects = [];
  fixture.threads = [];
  fixture.artifacts = [];
  fixture.bindings = [];
  fixture.calls = [];
});

const click = (element: Element | null | undefined) =>
  act(async () => {
    (element as HTMLElement).click();
  });
const menuItem = (label: string) =>
  [...document.querySelectorAll('[role="menuitem"]')].find((item) => item.textContent === label);
const openMenu = (label: string) => click(container.querySelector('[aria-label="' + label + '"]'));
const dialogOf = (className: string) => document.querySelector("." + className);
const buttonIn = (scope: Element | null, label: string) =>
  [...(scope?.querySelectorAll("button") ?? [])].find((item) => item.textContent === label) as
    HTMLButtonElement | undefined;
const typeName = (value: string) =>
  act(async () => {
    const input = dialogOf("rename-dialog")!.querySelector("input")!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
const callsOf = (method: string) =>
  fixture.calls.filter((call) => call.method === method).map((call) => call.input);

it("renames a project from its menu, and the list, the path and the home list show the new name", async () => {
  seed();
  await mount();
  await openMenu("레이더 지연 프로젝트 메뉴");
  await click(menuItem("이름 바꾸기"));
  const dialog = () => dialogOf("rename-dialog");
  expect(dialog()!.querySelector("input")!.value).toBe("레이더 지연");
  // The same name, and an empty one, cannot be saved.
  expect(buttonIn(dialog(), "저장")!.disabled).toBe(true);
  await typeName("   ");
  expect(buttonIn(dialog(), "저장")!.disabled).toBe(true);
  expect(callsOf("project/metadata/update")).toEqual([]);
  await typeName("레이더 지연 분석 2차");
  await click(buttonIn(dialog(), "저장"));
  await settle(() => dialog() === null);
  expect(callsOf("project/metadata/update")).toEqual([
    { project_id: "p", expected_revision: 3, name: "레이더 지연 분석 2차" },
  ]);
  await settle(
    () => container.querySelector(".project-button strong")?.textContent === "레이더 지연 분석 2차",
  );
  expect(container.querySelector(".context-path strong")?.textContent).toBe("레이더 지연 분석 2차");
  expect(
    container.querySelector('[aria-label="레이더 지연 분석 2차 프로젝트 메뉴"]'),
  ).not.toBeNull();
});

it("says so when the project changed in the meantime, and the next save uses the version just read", async () => {
  seed();
  await mount();
  fixture.projects[0].revision = 5; // changed elsewhere after this screen read the list
  await openMenu("레이더 지연 프로젝트 메뉴");
  await click(menuItem("이름 바꾸기"));
  await typeName("새 이름");
  await click(buttonIn(dialogOf("rename-dialog"), "저장"));
  await settle(() => dialogOf("rename-dialog")?.querySelector('[role="alert"]') != null);
  expect(dialogOf("rename-dialog")!.querySelector('[role="alert"]')!.textContent).toContain(
    "프로젝트가 그 사이 바뀌었습니다",
  );
  await settle(() => buttonIn(dialogOf("rename-dialog"), "저장")?.disabled === false);
  await click(buttonIn(dialogOf("rename-dialog"), "저장"));
  await settle(() => dialogOf("rename-dialog") === null);
  expect(callsOf("project/metadata/update").map((input) => input.expected_revision)).toEqual([
    3, 5,
  ]);
  expect(fixture.projects[0].name).toBe("새 이름");
});

it("renames a conversation: the list and the title show the name, and the question itself is not sent or changed", async () => {
  seed();
  await mount();
  expect(container.querySelector(".thread-button strong")?.textContent).toBe(
    "LAB-42의 지연 원인은?",
  );
  await openMenu("LAB-42의 지연 원인은? 대화 메뉴");
  await click(menuItem("이름 바꾸기"));
  expect(dialogOf("rename-dialog")!.textContent).toContain("질문 원문은 바뀌지 않습니다.");
  await typeName("LAB-42 지연");
  await click(buttonIn(dialogOf("rename-dialog"), "저장"));
  await settle(() => dialogOf("rename-dialog") === null);
  expect(callsOf("thread/metadata/update")).toEqual([
    { project_id: "p", thread_id: "t", expected_revision: 4, display_name: "LAB-42 지연" },
  ]);
  await settle(
    () => container.querySelector(".thread-button strong")?.textContent === "LAB-42 지연",
  );
  await settle(
    () => container.querySelector(".conversation-heading h1")?.textContent === "LAB-42 지연",
  );
  expect(container.querySelector(".context-path small")?.textContent).toBe("LAB-42 지연");
  expect(fixture.threads[0].problem).toBe("LAB-42의 지연 원인은?");
});

it("offers no rename for a conversation row that carries no version", async () => {
  seed();
  delete fixture.threads[0].revision;
  await mount();
  expect(container.querySelector(".thread-button strong")?.textContent).toBe(
    "LAB-42의 지연 원인은?",
  );
  expect(container.querySelector('[aria-label="LAB-42의 지연 원인은? 대화 메뉴"]')).toBeNull();
});

const sourceCards = () =>
  [...container.querySelectorAll("article.detail-card h3")].map((item) => item.textContent);
const openSources = async () => {
  const tab = [...container.querySelectorAll('.workspace-navigation [role="tab"]')].find(
    (item) => item.textContent === "자료·연결",
  );
  await click(tab);
  await settle(() => sourceCards().length > 0);
};

it("disconnects one source after asking: it leaves the list, the other stays, and the count follows", async () => {
  seed();
  await mount();
  expect(container.querySelector(".workspace-counts")?.textContent).toContain("자료 2");
  await openSources();
  expect(sourceCards()).toEqual(["radar-log.pdf", "weather.pdf"]);
  const card = [...container.querySelectorAll("article.detail-card")].find((item) =>
    item.textContent?.includes("weather.pdf"),
  )!;
  await click(buttonIn(card, "연결 해제"));
  const dialog = () => dialogOf("confirm-dialog");
  expect(dialog()!.textContent).toContain('"weather.pdf" 연결 해제');
  expect(dialog()!.textContent).toContain("파일과 이미 만든 답은 지워지지 않습니다.");
  expect(dialog()!.textContent).toContain('이미 만든 답은 모두 "재검토 필요"로 표시됩니다.');
  expect(callsOf("project/source/disconnect")).toEqual([]);
  await click(buttonIn(dialog(), "연결 해제"));
  await settle(() => dialog() === null);
  expect(callsOf("project/source/disconnect")).toEqual([
    { project_id: "p", binding_id: "binding:b", mode: "DETACH", expected_project_revision: 3 },
  ]);
  await settle(() => sourceCards().length === 1);
  expect(sourceCards()).toEqual(["radar-log.pdf"]);
  expect(container.textContent).toContain("연결을 해제한 자료 1개");
  await settle(
    () => container.querySelector(".workspace-counts")?.textContent?.includes("자료 1") === true,
  );
});

it("keeps the source connected when the person cancels", async () => {
  seed();
  await mount();
  await openSources();
  const card = [...container.querySelectorAll("article.detail-card")].find((item) =>
    item.textContent?.includes("weather.pdf"),
  )!;
  await click(buttonIn(card, "연결 해제"));
  await click(buttonIn(dialogOf("confirm-dialog"), "취소"));
  expect(dialogOf("confirm-dialog")).toBeNull();
  expect(callsOf("project/source/disconnect")).toEqual([]);
  expect(sourceCards()).toEqual(["radar-log.pdf", "weather.pdf"]);
});
