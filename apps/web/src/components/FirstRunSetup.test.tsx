// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { FirstRunSetup } from "./FirstRunSetup";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";
import { ModelCredentialPanel } from "./ModelCredentialPanel";

const fixture = vi.hoisted(() => ({
  calls: [] as { method: string; input: Record<string, unknown> }[],
  ready: {
    ready: false,
    deployment_mode: "LOCAL",
    model_connected: false,
    disclosure: "",
    setup: { internet_consent: "UNDECIDED" },
  } as Record<string, unknown>,
  readyError: null as string | null,
  deferReady: false,
  accounts: [] as { provider: string; label: string; connected: boolean; has_key: boolean; oauth: boolean;
    login_supported?: boolean; login_kind?: string; remote_auth_verified?: boolean | null; available_model_providers?: string[] }[],
  registerResult: {} as Record<string, unknown>,
  registerError: null as string | null,
}));

vi.mock("../api/rpcClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    if (method === "workspace/ready") {
      if (fixture.readyError) throw new Error(fixture.readyError);
      if (fixture.deferReady) return new Promise<never>(() => undefined);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.ready };
    }
    if (method === "project/list") return { state: "SUCCEEDED", operation_id: "", value: { projects: [] } };
    if (method === "model/credential/list") {
      return { state: "SUCCEEDED", operation_id: "", value: { accounts: fixture.accounts } };
    }
    if (method === "model/credential/register") {
      if (fixture.registerError) throw new Error(fixture.registerError);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.registerResult };
    }
    if (method === "workspace/setup/update") {
      fixture.ready = {
        ...fixture.ready,
        ready: true,
        setup: { internet_consent: input.internet_consent },
      };
      return { state: "SUCCEEDED", operation_id: "", value: fixture.ready };
    }
    throw new Error(`unexpected method ${method}`);
  },
}));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
function defaultAccounts() {
  return [
    { provider: "openai", label: "ChatGPT", connected: false, has_key: false, oauth: false, login_supported: true, login_kind: "codex_device_auth", remote_auth_verified: null, available_model_providers: [] },
    { provider: "anthropic", label: "Claude", connected: false, has_key: false, oauth: false, login_supported: false, login_kind: "unsupported", remote_auth_verified: null, available_model_providers: [] },
    { provider: "xai", label: "xAI", connected: false, has_key: false, oauth: false, login_supported: false, login_kind: "unsupported", remote_auth_verified: null, available_model_providers: [] },
  ];
}
async function flush() {
  await act(async () => {
    await tick();
  });
}

async function mount(entry: boolean | "panel" = false) {
  if (fixture.accounts.length === 0) fixture.accounts = defaultAccounts();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "test" }))));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        {entry === "panel" ? <ModelCredentialPanel projectId="project-example" /> : entry ? <LiveProjectWorkspace /> : <FirstRunSetup onDone={() => undefined} />}
      </QueryClientProvider>,
    );
  });
  await flush();
}

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  client?.clear();
  container?.remove();
  fixture.calls = [];
  fixture.readyError = null;
  fixture.deferReady = false;
  fixture.accounts = [];
  fixture.registerResult = {};
  fixture.registerError = null;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("treats an unsupported xAI login response as key guidance, without claiming login or readiness", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.registerResult = { started: false, provider: "xai", kind: "unsupported", reason_code: "MODEL_ACCOUNT_LOGIN_UNSUPPORTED", browser_url: "https://console.x.ai/" };
  const opened = vi.spyOn(window, "open").mockImplementation(() => null);
  await mount();
  const xaiRow = [...container.querySelectorAll(".first-run-provider")].find(row => row.textContent?.includes("xAI"))!;
  const consoleButton = [...xaiRow.querySelectorAll("button")].find(button => button.textContent === "키 발급 안내")!;
  await act(async () => consoleButton.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "xai" && !call.input.api_key)).toBe(true);
  expect(opened).not.toHaveBeenCalled();
  expect(container.textContent).toContain("계정 로그인 연결은 지원되지 않습니다");
  expect(container.textContent).toContain("사이트 방문만으로 연결되지는 않습니다");
  expect(container.querySelector('a[href="https://console.x.ai/"]')?.getAttribute("target")).toBe("_blank");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("does not restart an existing OAuth login to repair a missing model route", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: true, has_key: false, oauth: true,
    login_supported: true, login_kind: "codex_device_auth", remote_auth_verified: null, available_model_providers: ["codex-oauth"] }];
  await mount();
  expect(container.textContent).toContain("로그인 상태 확인됨");
  expect(container.textContent).toContain("실제 공급자 호출은 아직 확인되지 않았습니다");
  expect(container.textContent).toContain("재로그인 대신 모델 경로를 확인하세요");
  const login = [...container.querySelectorAll("button")].find(button => button.textContent === "로그인 확인됨") as HTMLButtonElement;
  expect(login.disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  fixture.ready = { ...fixture.ready, model_connected: true };
  const refresh = [...container.querySelectorAll("button")].find(button => button.textContent === "연결 상태 다시 확인")!;
  await act(async () => refresh.click()); await flush();
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(false);
});

it("shows a terminal-only device login response without claiming login started", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.registerResult = { started: false, provider: "codex-oauth", kind: "manual_device_auth", reason_code: "CODEX_DEVICE_AUTH_TERMINAL_REQUIRED" };
  await mount();
  const login = [...container.querySelectorAll("button")].find(button => button.textContent === "Codex 로그인 시작")!;
  await act(async () => login.click()); await flush();
  expect(container.textContent).toContain("codex login --device-auth");
  expect(container.textContent).toContain("THOTH는 로그인을 시작하거나 완료하지 않았습니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("does not infer login or readiness from an OMO-only legacy account row", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "xai", label: "xAI", connected: true, has_key: false, oauth: true }];
  await mount();
  const xaiRow = [...container.querySelectorAll(".first-run-provider")].find(row => row.textContent?.includes("xAI"))!;
  expect(xaiRow.textContent).toContain("연결 경로 확인 필요");
  expect(xaiRow.textContent).not.toContain("로그인 상태 확인됨");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("does not infer Codex login support from the OpenAI company name", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: false, has_key: false, oauth: false }];
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  const unknown = [...row.querySelectorAll("button")].find(button => button.textContent === "연결 방법 확인 중") as HTMLButtonElement;
  expect(unknown.disabled).toBe(true);
});

it("shows a missing Codex CLI as an error without claiming login started", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.registerError = "CODEX_CLI_NOT_FOUND";
  await mount();
  const login = [...container.querySelectorAll("button")].find(button => button.textContent === "Codex 로그인 시작")!;
  await act(async () => login.click()); await flush();
  expect(container.textContent).toContain("CODEX_CLI_NOT_FOUND");
  expect(container.textContent).not.toContain("계정 로그인 절차를 시작했습니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("calls THOTH key registration but does not claim provider validation from the key write", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.registerResult = { credential: { provider: "xai", model: "grok-test" } };
  await mount();
  const xaiRow = [...container.querySelectorAll(".first-run-provider")].find(row => row.textContent?.includes("xAI"))!;
  const keyButton = [...xaiRow.querySelectorAll("button")].find(button => button.textContent === "API 키")!;
  await act(async () => keyButton.click()); await flush();
  const input = xaiRow.querySelector('input[type="password"]') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "synthetic-test-key");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await flush();
  const register = [...xaiRow.querySelectorAll("button")].find(button => button.textContent === "키 등록")!;
  await act(async () => register.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "xai" && call.input.api_key === "synthetic-test-key")).toBe(true);
  expect(container.textContent).toContain("실제 모델 사용 가능 여부는 사용 시 확인됩니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("uses the same unsupported-login message in project settings", async () => {
  fixture.registerResult = { started: false, provider: "xai", kind: "unsupported", reason_code: "MODEL_ACCOUNT_LOGIN_UNSUPPORTED", browser_url: "https://console.x.ai/" };
  const opened = vi.spyOn(window, "open").mockImplementation(() => null);
  await mount("panel");
  const xaiRow = [...container.querySelectorAll(".company-account")].find(row => row.textContent?.includes("xAI"))!;
  const consoleButton = [...xaiRow.querySelectorAll("button")].find(button => button.textContent === "키 발급 안내")!;
  await act(async () => consoleButton.click()); await flush();
  expect(container.textContent).toContain("계정 로그인 연결은 지원되지 않습니다");
  expect(container.textContent).not.toContain("등록/로그인을 시작했습니다");
  expect(container.querySelector('a[href="https://console.x.ai/"]')).not.toBeNull();
  expect(opened).not.toHaveBeenCalled();
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.project_id === "project-example")).toBe(true);
});

it("keeps login and API key entry on the local first-run path", async () => {
  fixture.ready = {
    ready: false,
    deployment_mode: "LOCAL",
    model_connected: false,
    setup: { internet_consent: "UNDECIDED" },
  };
  await mount();
  expect(container.textContent).toContain("연구를 맡길 모델을 연결하세요");
  expect(container.textContent).toContain("로그인");
  expect(container.textContent).toContain("API 키");
  expect(fixture.calls.some((call) => call.method === "model/credential/list")).toBe(true);
});

it("shows disclosure and web consent only in hosted review", async () => {
  fixture.ready = {
    ready: false,
    deployment_mode: "HOSTED_REVIEW",
    model_connected: true,
    disclosure: "질문과 분석에 쓰인 자료는 운영자 OpenAI API로 전달됩니다.",
    hosted_model: { provider: "openai", model: "gpt-5.5" },
    setup: { internet_consent: "UNDECIDED" },
  };
  await mount();
  expect(container.textContent).toContain("심사 워크스페이스");
  expect(container.textContent).toContain("질문과 분석에 쓰인 자료는 운영자 OpenAI API로 전달됩니다.");
  expect(container.textContent).toContain("웹 자료 조회만 거부합니다");
  expect(container.textContent).toContain("로그인이나 API 키는 넣지 않습니다.");
  expect(container.textContent).not.toContain("연구를 맡길 모델을 연결하세요");
  expect(container.textContent).not.toContain("키 등록");
  expect(container.querySelector('input[type="password"]')).toBeNull();
  expect(fixture.calls.some((call) => call.method === "model/credential/list")).toBe(false);
  const deny = Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent?.includes("거부"),
  );
  expect(deny).toBeTruthy();
  await act(async () => {
    deny!.click();
  });
  await flush();
  expect(fixture.calls.some((call) => call.method === "workspace/setup/update" && call.input.internet_consent === "DENIED")).toBe(true);
});

it("keeps both first-run steps outside the workspace grid and enters the grid only after setup", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: true, has_key: true, oauth: false,
    login_supported: true, login_kind: "codex_device_auth", remote_auth_verified: null, available_model_providers: ["openai"] }];
  await mount(true);
  expect(container.querySelectorAll("main")).toHaveLength(1);
  expect(container.querySelector(".first-run-shell")?.closest(".conversation-workstation")).toBeNull();
  const next = [...container.querySelectorAll("button")].find(button => button.textContent === "다음");
  expect(next?.disabled).toBe(false);
  await act(async () => next!.click()); await flush();
  expect(container.textContent).toContain("2 / 2");
  expect(container.querySelectorAll("main")).toHaveLength(1);
  expect(container.querySelector(".conversation-workstation")).toBeNull();
  const deny = [...container.querySelectorAll("button")].find(button => button.textContent?.includes("거부"));
  await act(async () => deny!.click()); await flush();
  expect(container.querySelector(".first-run-shell")).toBeNull();
  expect(container.querySelector("main.conversation-workstation")).not.toBeNull();
  expect(fixture.calls.some(call => call.method === "workspace/setup/update" && call.input.internet_consent === "DENIED")).toBe(true);
});

it("does not constrain the readiness loading view to a sidebar cell", async () => {
  fixture.deferReady = true;
  await mount(true);
  expect(container.textContent).toContain("준비 확인 중");
  expect(container.querySelector(".conversation-workstation")).toBeNull();
});

it("keeps readiness errors and retry outside the workspace grid", async () => {
  fixture.readyError = "offline";
  await mount(true);
  expect(container.textContent).toContain("서버 준비 확인 실패");
  expect([...container.querySelectorAll("button")].some(button => button.textContent === "다시 확인")).toBe(true);
  expect(container.querySelectorAll("main")).toHaveLength(1);
  expect(container.querySelector(".conversation-workstation")).toBeNull();
});

it("removes ProjectPack and keeps hosted developer tools out of normal navigation", async () => {
  fixture.ready = {
    ready: true,
    deployment_mode: "HOSTED_REVIEW",
    model_connected: true,
    setup: { internet_consent: "DENIED" },
  };
  await mount(true);
  expect(container.textContent).not.toContain("ProjectPack");
  expect(container.textContent).not.toContain("개발자 도구");
  const operations = [...container.querySelectorAll("button")].find(button => button.textContent === "운영 정보");
  expect(operations).toBeTruthy();
  await act(async () => operations!.click());
  expect(container.textContent).toContain("운영 정보와 사용 현황은 프로젝트를 선택한 뒤 확인할 수 있습니다.");
  expect(container.querySelector("form.onboarding-card")).toBeNull();
});
