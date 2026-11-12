// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { saveWorkspaceContext } from "../api/research";
import { answerTopLayerSelectorsWithNo } from "../test/jsdomTopLayer";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

const WORKSPACE = `workspace:${"a".repeat(32)}`;
const fixture = vi.hoisted(() => ({
  calls: [] as string[],
  parserError: "TEXT_ENCODING_UNCONFIRMED" as string | null,
  listDelay: 300,
  succeed: false,
}));
vi.mock("../api/files", () => ({
  stageFile: async (file: File) => ({
    relative_path: `web/${file.name}`,
    filename: file.name,
    media_type: "text/plain",
    byte_sha256: "a".repeat(64),
    bytes: file.size,
  }),
}));
vi.mock("../api/rpcClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  return {
    ...actual,
    rpc: async (method: string, input: Record<string, unknown>) => {
      fixture.calls.push(method);
      let value: unknown = {};
      // The refusal exactly as the server sends it for a connector that fails to parse a file.
      if (method === "project/source/connect" && fixture.succeed)
        return { value: {}, state: "SUCCEEDED", operation_id: "op" };
      if (method === "project/source/connect")
        throw new actual.RpcError("source parsing failed", -32030, {
          connector_error: "PARSER_FAILURE",
          ...(fixture.parserError ? { parser_error: fixture.parserError } : {}),
        });
      if (method === "workspace/ready")
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
      if (method === "project/list") {
        await new Promise((resolve) => setTimeout(resolve, fixture.listDelay));
        value = {
          projects: [
            {
              project_id: "p",
              name: "old project",
              overlay: "test",
              lifecycle: "ACTIVE",
              revision: 1,
              cutoff_at: "2026-09-26T00:00:00Z",
            },
          ],
        };
      }
      if (method === "project/read")
        value = {
          project_id: input.project_id,
          name: "old project",
          lifecycle: "ACTIVE",
          revision: 1,
        };
      if (method === "thread/list")
        value = {
          threads: [
            {
              project_id: "p",
              thread_id: "t",
              updated_at: "2026-09-20T00:00:00Z",
              problem: "stored work",
              execution_state: "IDLE",
              lifecycle: "OPEN",
            },
          ],
        };
      if (method === "thread/read")
        value = {
          project_id: input.project_id,
          thread_id: input.thread_id,
          problem: "stored work",
          lifecycle: "OPEN",
          execution_state: "IDLE",
          current_object_ids: [],
          operation_state: "SUCCEEDED",
          current_result: null,
        };
      if (method === "thread/activity/list")
        value = { conversation: { turns: [], next_before_epoch: null, history_limited: false } };
      if (method === "project/source/list") value = { artifacts: [] };
      if (method === "evidence/list") value = { evidence: [] };
      if (method === "model/settings/read")
        value = {
          settings_digest: null,
          selection: { provider: null, model: null, reasoning_effort: null },
          effective_settings: null,
          availability: "UNAVAILABLE",
          reason_code: "MODEL_CAPABILITY_UNKNOWN",
          model_options: [],
        };
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
  for (let i = 0; i < 80; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 15));
    });
    if (check()) return;
  }
  throw new Error(`did not settle: ${container.textContent?.slice(0, 300)}`);
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
  fixture.calls = [];
  fixture.parserError = "TEXT_ENCODING_UNCONFIRMED";
  fixture.succeed = false;
});

async function openSourcesAndConnect() {
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
  await act(async () => {
    [...container.querySelectorAll('[role="tab"]')]
      .find((item) => item.textContent?.includes("자료"))!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await settle(() => Boolean(container.querySelector(".source-connect-panel")));
  const input = container.querySelector<HTMLInputElement>(
    '.source-connect-panel input[type="file"]',
  )!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["x"], "gb.txt")] });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => {
    (
      [...container.querySelectorAll(".source-connect-panel button")].find(
        (item) => item.textContent === "자료 연결",
      ) as HTMLButtonElement
    ).click();
  });
}

it("keeps the failure sentence on screen after the failed connect reads the project list again", async () => {
  await openSourcesAndConnect();
  await settle(() => fixture.calls.includes("project/source/connect"));
  const listsAfterFailure = () =>
    fixture.calls
      .slice(fixture.calls.indexOf("project/source/connect"))
      .filter((method) => method === "project/list").length;
  await settle(() => listsAfterFailure() > 0);
  await settle(() => container.querySelector(".source-connect-panel .bp6-callout") !== null);
  const callout = container.querySelector(".source-connect-panel .bp6-callout")!;
  expect(callout.textContent).toContain(
    "글자 인코딩을 확인하지 못했습니다. 파일을 UTF-8로 저장해 다시 올려 주세요.",
  );
  expect(callout.querySelector("details")!.textContent).toContain("TEXT_ENCODING_UNCONFIRMED");
});

it("keeps the success sentence on screen after the connect reads the project list again", async () => {
  fixture.succeed = true;
  await openSourcesAndConnect();
  await settle(() => fixture.calls.includes("project/source/connect"));
  await settle(() => container.querySelector(".source-connect-panel .bp6-callout") !== null);
  expect(container.querySelector(".source-connect-panel .bp6-callout")!.textContent).toContain(
    "자료 연결이 완료되었습니다",
  );
});

it("clears the failure when other files are chosen", async () => {
  await openSourcesAndConnect();
  await settle(() => container.querySelector(".source-connect-panel .bp6-callout") !== null);
  const input = container.querySelector<HTMLInputElement>(
    '.source-connect-panel input[type="file"]',
  )!;
  Object.defineProperty(input, "files", {
    configurable: true,
    value: [new File(["y"], "other.txt")],
  });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(container.querySelector(".source-connect-panel .bp6-callout")).toBeNull();
});
