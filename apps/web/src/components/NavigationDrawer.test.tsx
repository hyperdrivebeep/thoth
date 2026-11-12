// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { saveWorkspaceContext } from "../api/research";
import { answerTopLayerSelectorsWithNo } from "../test/jsdomTopLayer";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";
import { useNavigationDrawer } from "./useNavigationDrawer";

// The project list as a drawer (narrow windows). jsdom draws no layout, so this file fixes what the
// drawer does: open, close, where the focus goes. How it looks is measured in a browser (qa_b3.py).

const WORKSPACE = "workspace:" + "a".repeat(32);
const fixture = vi.hoisted(() => ({
  projects: [] as Record<string, unknown>[],
  threads: [] as Record<string, unknown>[],
}));
vi.mock("../api/rpcClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  return {
    ...actual,
    rpc: async (method: string, input: Record<string, unknown>) => {
      let value: unknown = {};
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
      if (method === "thread/list")
        value = {
          threads: fixture.threads
            .filter((item) => item.project_id === input.project_id)
            .map((item) => ({ ...item })),
        };
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
      if (method === "project/source/list")
        value = {
          artifacts: [],
          bindings: [],
          source_times: [],
          cutoff_basis: { cutoff_at: "2026-09-26T00:00:00Z", project_revision: 3 },
        };
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
  fixture.projects = [
    {
      project_id: "p",
      name: "레이더 지연 분석",
      overlay: "test",
      lifecycle: "ACTIVE",
      revision: 3,
      cutoff_at: "2026-09-26T00:00:00Z",
    },
    {
      project_id: "q",
      name: "근거가 부족한 질문",
      overlay: "test",
      lifecycle: "ACTIVE",
      revision: 1,
      cutoff_at: null,
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
});

const listButton = () =>
  container.querySelector(".workstation-topbar .nav-drawer-button") as HTMLButtonElement;
const list = () => container.querySelector("aside.navigation-rail") as HTMLElement;
const isOpen = () => list().getAttribute("data-open") === "true";
const backdrop = () => container.querySelector(".nav-drawer-backdrop") as HTMLElement | null;
const click = (element: Element | null | undefined) =>
  act(async () => {
    (element as HTMLElement).click();
  });
const press = (key: string, target: Element | null = document.activeElement) =>
  act(async () => {
    target!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
const projectButton = (name: string) =>
  [...list().querySelectorAll(".project-button")].find((item) =>
    item.textContent?.includes(name),
  ) as HTMLButtonElement;

it("opens the project list from the top bar, moves the focus into it, and Escape closes it and gives the focus back", async () => {
  await mount();
  expect(listButton().getAttribute("aria-label")).toBe("프로젝트 목록");
  expect(listButton().getAttribute("aria-controls")).toBe(list().id);
  expect(listButton().getAttribute("aria-expanded")).toBe("false");
  expect(isOpen()).toBe(false);
  expect(backdrop()).toBeNull();
  // Drawing the page does not put the focus on the list button.
  expect(document.activeElement).not.toBe(listButton());

  await click(listButton());
  expect(isOpen()).toBe(true);
  expect(listButton().getAttribute("aria-expanded")).toBe("true");
  expect(list().contains(document.activeElement)).toBe(true);
  expect(backdrop()).not.toBeNull();
  // The dimmed page takes a click but is not a control: it is not offered to a screen reader as one.
  expect(backdrop()!.getAttribute("role")).toBe("presentation");

  await press("Escape");
  expect(isOpen()).toBe(false);
  expect(listButton().getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(listButton());
  expect(backdrop()).toBeNull();
});

it("closes when the page outside the list is pressed", async () => {
  await mount();
  await click(listButton());
  await click(backdrop());
  expect(isOpen()).toBe(false);
  expect(document.activeElement).toBe(listButton());
});

it("closes after a project is chosen in it, and the top bar names that project", async () => {
  await mount();
  await click(listButton());
  await click(projectButton("근거가 부족한 질문"));
  expect(isOpen()).toBe(false);
  await settle(
    () => container.querySelector(".context-path strong")?.textContent === "근거가 부족한 질문",
  );
  expect(document.activeElement).toBe(listButton());
});

it("stays open when Escape closes a menu that was opened from it", async () => {
  await mount();
  await click(listButton());
  await click(list().querySelector('[aria-label="레이더 지연 분석 프로젝트 메뉴"]'));
  const item = document.querySelector('[role="menuitem"]');
  expect(item).not.toBeNull();
  // The menu is drawn outside the list and takes the key for itself.
  expect(list().contains(item)).toBe(false);
  await press("Escape", item);
  expect(isOpen()).toBe(true);
});

it("closes when the focus moves on into the page, and does not pull the focus back", async () => {
  await mount();
  await click(listButton());
  const question = container.querySelector("#live-problem") as HTMLTextAreaElement;
  await act(async () => {
    question.focus();
  });
  expect(isOpen()).toBe(false);
  expect(document.activeElement).toBe(question);
});

it("leaves the focus alone when a project is chosen while the list was never opened (wide window)", async () => {
  await mount();
  const other = projectButton("근거가 부족한 질문");
  await act(async () => {
    other.focus();
    other.click();
  });
  expect(isOpen()).toBe(false);
  expect(document.activeElement).not.toBe(listButton());
});

it("keeps the whole project name and cutoff in tooltips, for the widths where the bar shortens them", async () => {
  await mount();
  const name = container.querySelector(".context-path strong") as HTMLElement;
  expect(name.textContent).toBe("레이더 지연 분석");
  expect(name.title).toBe("레이더 지연 분석");
  const cutoff = container.querySelector(".header-status .header-cutoff") as HTMLElement;
  expect(cutoff.textContent).toContain("기준시점");
  expect(cutoff.title).toContain(cutoff.textContent!);
  await settle(
    () => container.querySelector(".header-health")?.getAttribute("data-health") === "ok",
  );
});

it("gives the focus back to what opened a side panel only after the panel is gone", async () => {
  // In a narrow window the panel covers the conversation and the stylesheet hides what is under it,
  // so the opener can only take the focus once the page has been drawn without the panel.
  await mount();
  const attach = container.querySelector(".composer-tools button") as HTMLButtonElement;
  await act(async () => {
    attach.focus();
    attach.click();
  });
  expect(container.querySelector(".conversation-layout")!.classList.contains("detail-open")).toBe(
    true,
  );
  const coveredWhenFocused: boolean[] = [];
  const focus = attach.focus.bind(attach);
  attach.focus = () => {
    coveredWhenFocused.push(
      container.querySelector(".conversation-layout")!.classList.contains("detail-open"),
    );
    focus();
  };
  await click(container.querySelector(".research-side-panel header button:last-child"));
  expect(container.querySelector(".research-side-panel")).toBeNull();
  expect(coveredWhenFocused).toEqual([false]);
  expect(document.activeElement).toBe(attach);
});

const escapesSeenByPage: string[] = [];
function ListWithButton() {
  const drawer = useNavigationDrawer();
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- stands for a page that listens for the Escape that bubbles up to it; the test checks the drawer keeps that key to itself
    <main
      onKeyDown={(event) => {
        if (event.key === "Escape") escapesSeenByPage.push("Escape");
      }}
    >
      <aside {...drawer.railProps}>
        <button type="button">in the list</button>
      </aside>
      <button type="button" ref={drawer.button} onClick={drawer.toggle}>
        list
      </button>
    </main>
  );
}

it("does not take the focus when a page that already shows the list button is first drawn, and keeps Escape only while open", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  escapesSeenByPage.length = 0;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(<ListWithButton />));
  const [inList, button] = [...container.querySelectorAll("button")];
  expect(document.activeElement).toBe(document.body);
  // Closed (a wide window shows the list all the time): Escape pressed in the list is the page's to handle.
  await press("Escape", inList);
  expect(escapesSeenByPage).toHaveLength(1);
  await click(button);
  expect(document.activeElement).toBe(inList);
  // Open: Escape closes the list and goes no further.
  await press("Escape", inList);
  expect(escapesSeenByPage).toHaveLength(1);
  expect(document.activeElement).toBe(button);
  await click(button);
  await click(button);
  expect(document.activeElement).toBe(button);
});
