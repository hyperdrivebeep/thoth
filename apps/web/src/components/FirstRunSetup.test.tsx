// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { FirstRunSetup } from "./FirstRunSetup";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";
import { ModelCredentialPanel } from "./ModelCredentialPanel";
import { localCredentialWorkspaceId } from "./localCredentialWorkspace";
import serializedLocalReady from "./localWorkspaceReady.fixture.json";
import authContractFixture from "./credentialAuthMethods.fixture.json";

const fixture = vi.hoisted(() => ({
  calls: [] as { method: string; input: Record<string, unknown> }[],
  ready: {
    ready: false,
    model_connected: false,
    disclosure: "",
    setup: { schema_version: 1, revision: 1, internet_consent: "UNDECIDED", internet_grant_id: null },
    setup_complete: false,
    workspace_readable: true,
    execution_ready: false,
    workspace_id: "workspace:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  } as Record<string, unknown>,
  readyError: null as string | null,
  deferReady: false,
  accounts: [] as { provider: string; label: string; connected: boolean; has_key: boolean; oauth: boolean;
    login_supported?: boolean; login_kind?: string; remote_auth_verified?: boolean | null; available_model_providers?: string[];
    connection_state?: string; profile_mode?: string; reason_code?: string | null; guidance?: string | null;
    execution_eligible?: boolean | null; execution_verified?: boolean | null }[],
  registerResult: {} as Record<string, unknown>,
  registerError: null as string | null,
  loginStatusResult: {} as Record<string, unknown>,
  loginStatusError: null as string | null,
  cancelResult: {} as Record<string, unknown>,
  cancelError: null as string | null,
  completeResult: {} as Record<string, unknown>,
  completeError: null as string | null,
  refreshError: null as string | null,
  catalogStatus: [] as unknown[],
  refreshEffect: null as null | (() => void),
  installError: null as string | null,
  installEffect: null as null | (() => void),
  installGate: null as null | Promise<void>,
  deferredStatus: null as null | (() => void),
  holdStatus: false,
  forbidNetwork: false,
  // Requests the fake server has received and not yet answered; the tests wait for it to reach 0.
  inflight: 0,
}));

vi.mock("../api/rpcClient", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/rpcClient")>();
  const answer = async (method: string, input: Record<string, unknown>) => {
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
      // "RPC:<REASON>" simulates the server's typed JSON-RPC rejection.
      if (fixture.registerError) throw fixture.registerError.startsWith("RPC:")
        ? new original.RpcError(fixture.registerError.slice(4), -32030, {}) : new Error(fixture.registerError);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.registerResult };
    }
    if (method === "model/credential/login/status") {
      if (fixture.holdStatus) await new Promise<void>((resolve) => { fixture.deferredStatus = resolve; });
      if (fixture.loginStatusError) throw new Error(fixture.loginStatusError);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.loginStatusResult };
    }
    if (method === "model/credential/login/cancel") {
      if (fixture.cancelError) throw new Error(fixture.cancelError);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.cancelResult };
    }
    if (method === "model/credential/login/complete") {
      if (fixture.completeError) throw new Error(fixture.completeError);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.completeResult };
    }
    if (method === "model/tooling/install") {
      if (fixture.installGate) await fixture.installGate;
      if (fixture.installError) throw new original.RpcError(fixture.installError.slice(4), -32030, {});
      fixture.installEffect?.();
      return { state: "SUCCEEDED", operation_id: "", value: { tool_id: input.tool_id, installed: true, version: "test",
        accounts: fixture.accounts } };
    }
    if (method === "model/catalog/refresh") {
      if (fixture.refreshError) throw new original.RpcError(fixture.refreshError, -32030, {});
      fixture.refreshEffect?.();
      return { state: "SUCCEEDED", operation_id: "", value: { accounts: fixture.accounts, credentials: [],
        model_option_count: 1, catalog_refreshed: true, catalog_status: fixture.catalogStatus } };
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
  };
  return {
    ...original,
    rpc: async (method: string, input: Record<string, unknown>) => {
      // A response the test deliberately holds back is not waited for.
      const held = (method === "workspace/ready" && fixture.deferReady)
        || (method === "model/credential/login/status" && fixture.holdStatus)
        || (method === "model/tooling/install" && fixture.installGate !== null);
      if (held) return answer(method, input);
      fixture.inflight += 1;
      try { return await answer(method, input); } finally { fixture.inflight -= 1; }
    },
  };
});

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
function defaultAccounts() {
  return [
    { provider: "openai", label: "ChatGPT", connected: false, has_key: false, oauth: false, login_supported: true, login_kind: "codex_isolated_browser", remote_auth_verified: null, available_model_providers: [] },
    { provider: "anthropic", label: "Claude", connected: false, has_key: false, oauth: false, login_supported: false, login_kind: "unsupported", remote_auth_verified: null, available_model_providers: [] },
    { provider: "xai", label: "xAI", connected: false, has_key: false, oauth: false, login_supported: false, login_kind: "unsupported", remote_auth_verified: null, available_model_providers: [] },
  ];
}
const WORKSPACE_A = serializedLocalReady.workspace_id;
const WORKSPACE_B = "workspace:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
function localReady(overrides: Record<string, unknown> = {}) {
  return { ...serializedLocalReady, setup: { ...serializedLocalReady.setup }, ...overrides };
}
function unavailableSetupReady(status: "CORRUPT" | "UNREADABLE") {
  return { ready: false, setup: null, setup_complete: false, model_connected: false,
    workspace_readable: true, execution_ready: false, workspace_id: WORKSPACE_A,
    setup_status: status, reason_code: status === "CORRUPT" ? "WORKSPACE_SETUP_CORRUPT" : "WORKSPACE_SETUP_UNREADABLE" };
}
function contractAccounts() {
  return authContractFixture.accounts.map(row => ({ ...row,
    available_model_providers: [...row.available_model_providers],
    auth_methods: row.auth_methods.map(method => ({ ...method, capabilities: { ...method.capabilities } })) }));
}
function xaiAccount(overrides: Record<string, unknown> = {}) {
  return { provider: "xai", label: "xAI", connected: false, has_key: false, oauth: false,
    login_supported: true, login_kind: "xai_device_code", connection_state: "LOGIN_REQUIRED",
    available_model_providers: [], execution_eligible: false, execution_verified: false, ...overrides };
}
function xaiStart(expiresInSeconds = 300) {
  return { started: true, provider: "xai", account_provider: "xai", auth_method: "xai_device_code",
    kind: "xai_device_code", login_id: "login_test_1", state: "PENDING", login_state: "PENDING",
    auth_state: "DISCONNECTED", catalog_state: "UNAVAILABLE",
    user_code: "ABCD-EFGH", verification_uri: "https://grok.com/activate",
    expires_at: Date.now() / 1000 + expiresInSeconds };
}
function xaiStatus(state: string) {
  return { provider: "xai", account_provider: "xai", auth_method: "xai_device_code",
    kind: "xai_device_code", login_id: "login_test_1", state, login_state: state,
    auth_state: state === "CONNECTED" ? "CONNECTED" : "DISCONNECTED", catalog_state: state === "CONNECTED" ? "AVAILABLE" : "UNAVAILABLE" };
}

it("accepts only the real LOCAL readiness shape or an explicit LOCAL mode", () => {
  expect(localCredentialWorkspaceId(localReady())).toBe(WORKSPACE_A);
  expect(localCredentialWorkspaceId(localReady({ deployment_mode: "LOCAL" }))).toBe(WORKSPACE_A);
  for (const value of [
    localReady({ deployment_mode: "HOSTED_REVIEW" }), localReady({ deployment_mode: "OTHER" }),
    localReady({ deployment_mode: null }), localReady({ deployment_mode: undefined }),
    localReady({ workspace_readable: false }), localReady({ workspace_readable: undefined }),
    localReady({ workspace_id: "workspace-A" }), localReady({ workspace_id: null }),
    { ready: false, model_connected: false, setup: { internet_consent: "UNDECIDED" } },
  ]) expect(localCredentialWorkspaceId(value)).toBeUndefined();
});

it.each([
  ["CORRUPT", "설정 파일이 손상"],
  ["UNREADABLE", "설정 파일을 읽지 못"],
] as const)("names %s workspace setup failure instead of presenting it as first setup", async (status, expected) => {
  fixture.forbidNetwork = true;
  fixture.ready = unavailableSetupReady(status);
  await mount();
  expect(container.textContent).toContain(expected);
  expect(container.textContent).not.toContain("공개 웹을 이 워크스페이스에서 쓸까요");
  expect(fixture.calls.some(call => call.method === "workspace/setup/update" || call.method === "model/credential/register")).toBe(false);
  const refresh = [...container.querySelectorAll("button")].find(button => button.textContent === "설정 상태 다시 확인")!;
  await act(async () => refresh.click()); await flush();
  expect(fixture.calls.filter(call => call.method === "workspace/ready").length).toBeGreaterThan(1);
  expect(fixture.calls.some(call => call.method === "workspace/setup/update" || call.method === "model/credential/register")).toBe(false);
});

it("keeps a normal MISSING setup state on the existing first-run path", async () => {
  fixture.ready = localReady({ setup_status: "MISSING" });
  await mount();
  expect(container.textContent).toContain("연구를 맡길 모델을 연결하세요");
  expect(container.textContent).not.toContain("기존 작업 공간 설정을 확인하세요");
  expect(fixture.calls.some(call => call.method === "workspace/setup/update")).toBe(false);
});
/** Lets the screen show what the fake server answered: waits until every request has been answered
 *  and no new one starts for several ticks in a row (not a fixed time, which a slow machine overruns;
 *  and not a single idle reading, because the screen sends the next request a moment after a response). */
async function flush() {
  await act(async () => {
    let quiet = 0;
    let seen = fixture.calls.length;
    while (quiet < 3) {
      await tick();
      const idle = fixture.inflight === 0 && fixture.calls.length === seen;
      seen = fixture.calls.length;
      quiet = idle ? quiet + 1 : 0;
    }
  });
}

async function mount(entry: boolean | "panel" = false) {
  if (fixture.accounts.length === 0) fixture.accounts = defaultAccounts();
  localStorage.clear(); sessionStorage.clear();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", fixture.forbidNetwork
    ? vi.fn(async () => { throw new Error("unhandled network request in xAI UI test"); })
    : vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "test" }))));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        {entry === "panel" ? <ModelCredentialPanel projectId="project-example" workspaceId={localCredentialWorkspaceId(fixture.ready)} />
          : entry ? <LiveProjectWorkspace /> : <FirstRunSetup onDone={() => undefined} />}
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
  fixture.loginStatusResult = {};
  fixture.loginStatusError = null;
  fixture.cancelResult = {};
  fixture.cancelError = null;
  fixture.completeResult = {};
  fixture.completeError = null;
  fixture.refreshError = null;
  fixture.catalogStatus = [];
  fixture.refreshEffect = null;
  fixture.installError = null;
  fixture.installEffect = null;
  fixture.installGate = null;
  fixture.deferredStatus = null;
  fixture.holdStatus = false;
  fixture.inflight = 0;
  fixture.forbidNetwork = false;
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

it("starts xAI device login on first run and confirms it without changing the model choice", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  const opened = vi.spyOn(window, "open").mockImplementation(() => null);
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  const login = [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!;
  await act(async () => login.click()); await flush();
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai" && call.input.api_key === "")).toHaveLength(1);
  expect(row.textContent).toContain("ABCD-EFGH");
  expect(row.querySelector('a[href="https://grok.com/activate"]')).not.toBeNull();
  expect(row.textContent).toContain("로그인 시작만으로 연결된 것은 아닙니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  expect(opened).not.toHaveBeenCalled();
  expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled();
  const browserStorage = Object.keys(localStorage).map(key => localStorage.getItem(key)).join(" ")
    + Object.keys(sessionStorage).map(key => sessionStorage.getItem(key)).join(" ");
  expect(browserStorage).not.toMatch(/ABCD-EFGH|login_test_1|grok\.com\/activate/);
  expect(window.location.search).toBe("");

  fixture.loginStatusResult = xaiStatus("CONNECTED");
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai"
    ? xaiAccount({ connected: true, oauth: true, connection_state: "EXECUTION_UNVERIFIED",
      execution_eligible: true, available_model_providers: ["xai-oauth"] }) : account);
  fixture.ready = { ...fixture.ready, model_connected: true };
  const check = [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!;
  await act(async () => check.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/login/status" && call.input.login_id === "login_test_1")).toBe(true);
  // The row says once that the real run is not confirmed yet (it used to say it in the login box and again in the note).
  expect(row.textContent).toContain("xAI 로그인이 확인됐습니다");
  expect(row.textContent?.match(/실행 성공은 별도 확인|(확인|검증)되지 않았/g)).toHaveLength(1);
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
  expect(opened).not.toHaveBeenCalled();
});

it("keeps xAI API-key and Codex routes separate in the project account panel", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ ready: true, model_connected: true, setup_complete: true, execution_ready: true,
    setup: { schema_version: 1, revision: 1, internet_consent: "DENIED", internet_grant_id: null } });
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai"
    ? xaiAccount({ connected: true, has_key: true, connection_state: "API_KEY_AVAILABLE",
      execution_eligible: true, available_model_providers: ["xai"] }) : account);
  fixture.registerResult = xaiStart();
  await mount("panel");
  expect(fixture.calls.some(call => call.method === "workspace/ready")).toBe(false);
  const row = [...container.querySelectorAll(".company-account")].find(item => item.textContent?.includes("xAI"))!;
  expect(row.querySelector('input[type="password"]')).not.toBeNull();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click());
  await flush();
  expect(row.textContent).toContain("ABCD-EFGH");
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.project_id === "project-example"
    && call.input.provider === "xai" && call.input.api_key === "")).toBe(true);
  expect([...container.querySelectorAll("button")].some(button => button.textContent === "Codex 로그인 시작")).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("starts the explicit xAI device method beside an existing API key", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ model_connected: true });
  const accounts = contractAccounts();
  accounts[1].has_key = true;
  accounts[1].oauth = false;
  accounts[1].available_model_providers = ["xai"];
  accounts[1].auth_methods[0].connected = true;
  accounts[1].auth_methods[0].execution_eligible = true;
  accounts[1].auth_methods[1].connected = false;
  accounts[1].auth_methods[1].execution_eligible = false;
  fixture.accounts = accounts;
  fixture.registerResult = xaiStart();
  await mount("panel");
  const row = [...container.querySelectorAll(".company-account")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "xai"
    && call.input.auth_method === "xai_device_code" && call.input.api_key === "")).toBe(true);
  expect(row.textContent).toContain("API 키: 등록됨");
  expect(row.textContent).toContain("로그인 시도: PENDING · 저장 인증: DISCONNECTED · 모델 목록: UNAVAILABLE");
  expect(row.querySelector('a[href="https://grok.com/activate"]')).not.toBeNull();
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("keeps an xAI method disabled when its start capability is false", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  const accounts = contractAccounts();
  accounts[1].oauth = false;
  accounts[1].connected = false;
  accounts[1].auth_methods[1].connected = false;
  accounts[1].auth_methods[1].capabilities.start = false;
  accounts[1].auth_methods[1].reason_code = "XAI_CLIENT_UNSUPPORTED";
  fixture.accounts = accounts;
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  // A start-incapable method offers no start control; the reason is stated instead.
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "xAI로 로그인")).toBe(false);
  expect(row.textContent).toContain("이 PC에서는 xAI 로그인을 시작할 수 없습니다");
  expect(row.textContent).toContain("XAI_CLIENT_UNSUPPORTED");
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "xai")).toBe(false);
});

it.each([
  ["first-run unreadable workspace", false, localReady({ workspace_readable: false })],
  ["first-run malformed workspace ID", false, localReady({ workspace_id: "workspace-A" })],
  ["account panel hosted mode", true, localReady({ deployment_mode: "HOSTED_REVIEW" })],
  ["account panel unknown mode", true, localReady({ deployment_mode: null })],
] as const)("blocks xAI login for %s", async (_label, panel, readiness) => {
  fixture.forbidNetwork = true;
  fixture.ready = readiness;
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  await mount(panel ? "panel" : false);
  const selector = panel ? ".company-account" : ".first-run-provider";
  // Readiness enables a second query; wait for its rendered control instead of
  // assuming one fixed tick is enough under a concurrent full-suite run.
  await vi.waitFor(async () => {
    await flush();
    const renderedRow = [...container.querySelectorAll(selector)].find(item => item.textContent?.includes("xAI"));
    expect([...renderedRow?.querySelectorAll("button") ?? []].some(button => button.textContent === "xAI로 로그인")).toBe(true);
  });
  const row = [...container.querySelectorAll(selector)].find(item => item.textContent?.includes("xAI"))!;
  const login = [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인") as HTMLButtonElement;
  expect(login.disabled).toBe(true);
  expect(row.textContent).toContain("작업 공간을 확인한 뒤 xAI 로그인을 시작할 수 있습니다");
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "xai")).toBe(false);
});

it("does not treat an xAI OAuth login without its model route as ready", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ model_connected: true });
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai"
    ? xaiAccount({ connected: true, oauth: true, connection_state: "CATALOG_UNAVAILABLE",
      execution_eligible: false, available_model_providers: [] }) : account);
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  expect(row.textContent).toContain("로그인은 확인됐지만 사용할 모델 경로가 아직 없습니다");
  expect(row.textContent).not.toContain("모델 목록에서 xai-oauth 경로를 직접 선택할 수 있습니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
});

it("cancels only the current xAI login id and leaves the key path visible", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  fixture.cancelResult = xaiStatus("CANCELLED");
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "이 로그인 취소")!.click()); await flush();
  expect(fixture.calls.filter(call => call.method === "model/credential/login/cancel")).toEqual([
    { method: "model/credential/login/cancel", input: { provider: "xai", auth_method: "xai_device_code", login_id: "login_test_1" } },
  ]);
  expect(row.textContent).toContain("기존 API 키 연결은 유지됩니다");
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "API 키로 연결")).toBe(true);
});

it("checks the same xAI login after an uncertain cancel without issuing another cancel", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  fixture.cancelError = "synthetic response loss";
  fixture.loginStatusResult = xaiStatus("PENDING");
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "이 로그인 취소")!.click()); await flush();
  expect(row.textContent).toContain("취소 결과를 확인하지 못했습니다");
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(fixture.calls.filter(call => call.method === "model/credential/login/cancel")).toHaveLength(1);
  expect(fixture.calls.filter(call => call.method === "model/credential/login/status")).toHaveLength(1);
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai")).toHaveLength(1);
});

it("ignores a late xAI status response after the workspace changes", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  fixture.holdStatus = true;
  await act(async () => { [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click(); });
  await flush();
  await vi.waitFor(() => expect(fixture.deferredStatus).not.toBeNull());
  fixture.ready = { ...fixture.ready, workspace_id: WORKSPACE_B };
  await act(async () => { await client.invalidateQueries({ queryKey: ["workspace-ready"] }); });
  await flush();
  fixture.loginStatusResult = xaiStatus("CONNECTED");
  fixture.deferredStatus!();
  await flush();
  expect(container.textContent).not.toContain("ABCD-EFGH");
  expect(container.textContent).not.toContain("xAI 로그인이 확인됐습니다");
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("clears a project account panel's xAI code when the LOCAL workspace changes", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  await mount("panel");
  const row = [...container.querySelectorAll(".company-account")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  expect(row.textContent).toContain("ABCD-EFGH");
  fixture.ready = localReady({ workspace_id: WORKSPACE_B });
  await act(async () => { root.render(<QueryClientProvider client={client}>
    <ModelCredentialPanel projectId="project-example" workspaceId={localCredentialWorkspaceId(fixture.ready)} />
  </QueryClientProvider>); });
  await flush();
  expect(container.textContent).not.toContain("ABCD-EFGH");
  expect(container.querySelector('a[href="https://grok.com/activate"]')).toBeNull();
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai")).toHaveLength(1);
});

it("hides a pending xAI code when the workspace readiness read is denied", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  expect(row.textContent).toContain("ABCD-EFGH");
  fixture.readyError = "AUTHORIZATION_DENIED";
  await act(async () => { await client.invalidateQueries({ queryKey: ["workspace-ready"] }); });
  await flush();
  expect(container.textContent).not.toContain("ABCD-EFGH");
  expect(container.querySelector('a[href="https://grok.com/activate"]')).toBeNull();
  expect(fixture.calls.some(call => call.method === "model/credential/login/status")).toBe(false);
});

it("stops xAI device polling when its Unix-seconds expiry passes", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  await mount();
  // The short expiry runs from the start response, so it is set after the slow mount, not before.
  fixture.registerResult = xaiStart(0.3);
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
  expect(row.textContent).toContain("유효 시간이 끝났습니다");
  expect(row.querySelector('a[href="https://grok.com/activate"]')).toBeNull();
  expect(fixture.calls.some(call => call.method === "model/credential/login/status")).toBe(false);
});

it("stops automatic status checks on a network error without starting another xAI login", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  fixture.loginStatusError = "synthetic network loss";
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("자동 확인을 멈췄습니다");
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai")).toHaveLength(1);
  expect(fixture.calls.filter(call => call.method === "model/credential/login/status")).toHaveLength(1);
});

it("rejects an untrusted xAI approval URI before rendering a link or polling", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = { ...xaiStart(), verification_uri: "https://evil.example/activate" };
  const opened = vi.spyOn(window, "open").mockImplementation(() => null);
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  expect(row.textContent).toContain("시작 응답을 확인하지 못했습니다");
  expect(row.querySelector("a")).toBeNull();
  expect(fixture.calls.some(call => call.method === "model/credential/login/status")).toBe(false);
  expect(opened).not.toHaveBeenCalled();
  expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled();
});

it("automatically polls one xAI login and stops after a terminal result", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  fixture.loginStatusResult = xaiStatus("DENIED");
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5200)); });
  expect(row.textContent).toContain("xAI 로그인이 거부됐습니다");
  expect(fixture.calls.filter(call => call.method === "model/credential/login/status")).toHaveLength(1);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5200)); });
  expect(fixture.calls.filter(call => call.method === "model/credential/login/status")).toHaveLength(1);
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai")).toHaveLength(1);
}, 15000);

it("shows unknown xAI login states without claiming a connection or retrying", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  fixture.loginStatusResult = xaiStatus("NEW_UNRECOGNIZED_STATE");
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("현재 로그인 요청의 상태를 확인하지 못했습니다");
  expect(row.textContent).not.toContain("xAI 로그인이 확인됐습니다");
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai")).toHaveLength(1);
});

it("shows xAI slow-down and a later login-required state without claiming success", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = defaultAccounts().map((account) => account.provider === "xai" ? xaiAccount() : account);
  fixture.registerResult = xaiStart();
  fixture.loginStatusResult = xaiStatus("SLOW_DOWN");
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "xAI로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("상태 확인 간격을 늘렸습니다");
  fixture.loginStatusResult = { provider: "xai", kind: "xai_device_code", state: "LOGIN_REQUIRED" };
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("현재 로그인 요청을 확인할 수 없습니다");
  expect(row.textContent).not.toContain("xAI 로그인이 확인됐습니다");
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "xai")).toHaveLength(1);
});

it("keeps API-key and OAuth method rows separate when both are connected", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ model_connected: true });
  const accounts = contractAccounts();
  accounts[0].oauth = true;
  accounts[0].available_model_providers = ["openai", "codex-oauth"];
  accounts[0].auth_methods[1].connected = true;
  accounts[0].auth_methods[1].execution_eligible = true;
  accounts[1].has_key = true;
  accounts[1].available_model_providers = ["xai", "xai-oauth"];
  accounts[1].auth_methods[0].connected = true;
  accounts[1].auth_methods[0].execution_eligible = true;
  fixture.accounts = accounts;
  await mount();
  const openai = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  const xai = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("xAI"))!;
  expect(openai.textContent).toContain("API 키: 등록됨");
  expect(openai.textContent).toContain("codex-oauth");
  expect(xai.textContent).toContain("API 키: 등록됨");
  expect(xai.textContent).toContain("xai-oauth");
  expect([...openai.querySelectorAll("button")].find(button => button.textContent === "ChatGPT 로그인됨")?.disabled).toBe(true);
  expect([...xai.querySelectorAll("button")].find(button => button.textContent === "xAI 로그인됨")?.disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/settings/update" || call.method === "model/credential/register")).toBe(false);
  const next = [...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement;
  expect(next.disabled).toBe(false);
  await act(async () => next.click());
  expect(container.textContent).toContain("허용 또는 거부를 고르면 프로젝트로 갑니다");
});

it("shows the disabled client-ID route as a plain reason without the old Claude Code guidance button", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = contractAccounts();
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "Claude로 로그인")).toBe(false);
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "Claude Code 연결 안내")).toBe(false);
  expect(row.textContent).toContain("CLAUDE_CLIENT_REGISTRATION_REQUIRED");
  expect(row.textContent).toContain("Claude 구독 로그인은 이 PC에서 아직 켜지지 않았습니다");
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
  expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled();
});

it("shows a Claude OAuth method separately from the key route without selecting a model", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ model_connected: true });
  const accounts = contractAccounts();
  accounts[2].login_supported = true;
  accounts[2].connected = true;
  accounts[2].oauth = true;
  accounts[2].execution_eligible = true;
  accounts[2].available_model_providers = ["claude-oauth"];
  accounts[2].auth_methods[1].connected = true;
  accounts[2].auth_methods[1].execution_eligible = true;
  accounts[2].auth_methods[1].capabilities.start = true;
  fixture.accounts = accounts;
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;
  expect(row.textContent).toContain("API 키: 미등록");
  expect(row.textContent).toContain("claude-oauth");
  expect(row.textContent).toContain("실제 제3자 사용 허용과 모델 실행 성공은 아직 확인되지 않았습니다");
  expect([...row.querySelectorAll("button")].find(button => button.textContent === "Claude 로그인됨")?.disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/settings/update" || call.method === "model/credential/register")).toBe(false);
});

it("starts explicit Claude PKCE and clears a manually submitted response from the screen", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  const accounts = contractAccounts();
  accounts[2].login_supported = true;
  accounts[2].auth_methods[1].capabilities.start = true;
  fixture.accounts = accounts;
  fixture.registerResult = { started: true, provider: "anthropic", account_provider: "anthropic", auth_method: "claude_pkce",
    route: "claude-oauth", kind: "oauth", login_id: "login:synthetic-claude", login_state: "PENDING",
    auth_state: "DISCONNECTED", catalog_state: "UNAVAILABLE", expires_at: Math.floor(Date.now() / 1000) + 300,
    authorization_url: "https://claude.ai/oauth/authorize?state=synthetic-state",
    capabilities: { start: true, status: true, cancel: true, manual_complete: true } };
  fixture.completeResult = { ...fixture.registerResult, started: false, login_state: "PENDING", authorization_url: undefined };
  const opened = vi.spyOn(window, "open").mockImplementation(() => null);
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "anthropic"
    && call.input.auth_method === "claude_pkce")).toBe(true);
  expect(row.textContent).toContain("로그인 시도: PENDING · 저장 인증: DISCONNECTED · 모델 목록: UNAVAILABLE");
  expect(row.querySelector('a[href^="https://claude.ai/oauth/authorize"]')).not.toBeNull();
  expect(opened).not.toHaveBeenCalled();
  const stored = Object.keys(localStorage).map(key => localStorage.getItem(key)).join(" ")
    + Object.keys(sessionStorage).map(key => sessionStorage.getItem(key)).join(" ");
  expect(stored).not.toContain("synthetic-state");
  expect(window.location.search).toBe("");
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "코드·리디렉션 URL 직접 입력")!.click());
  const input = row.querySelector<HTMLInputElement>('[aria-label="Claude 인증 응답"]')!;
  const secret = "https://localhost:53692/callback?code=synthetic-code&state=synthetic-state";
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, secret);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "이 시도에 제출")!.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/login/complete" && call.input.provider === "anthropic"
    && call.input.auth_method === "claude_pkce" && call.input.login_id === "login:synthetic-claude"
    && call.input.response === secret)).toBe(true);
  expect(input.value).toBe("");
  expect(row.textContent).not.toContain(secret);
  expect(Object.values(localStorage).join(" ") + Object.values(sessionStorage).join(" ")).not.toContain(secret);
  expect(opened).not.toHaveBeenCalled();
  expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled();
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("keeps a failed Claude manual response out of errors and browser storage", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  const accounts = contractAccounts();
  accounts[2].login_supported = true;
  accounts[2].auth_methods[1].capabilities.start = true;
  fixture.accounts = accounts;
  fixture.registerResult = { started: true, provider: "anthropic", account_provider: "anthropic", auth_method: "claude_pkce",
    kind: "oauth", login_id: "login:synthetic-claude", login_state: "PENDING", auth_state: "DISCONNECTED",
    catalog_state: "UNAVAILABLE", capabilities: { start: true, status: true, cancel: true, manual_complete: true } };
  fixture.completeError = "synthetic rejected response containing secret-code";
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "코드·리디렉션 URL 직접 입력")!.click());
  const input = row.querySelector<HTMLInputElement>('[aria-label="Claude 인증 응답"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "secret-code");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "이 시도에 제출")!.click()); await flush();
  expect(input.value).toBe("");
  expect(row.textContent).not.toContain("secret-code");
  expect(row.textContent).toContain("수동 응답 결과를 확인하지 못했습니다");
  expect(Object.keys(localStorage).map(key => localStorage.getItem(key)).join(" ")).not.toContain("secret-code");
  expect(fixture.calls.filter(call => call.method === "model/credential/login/complete")).toHaveLength(1);
  expect(vi.mocked(globalThis.fetch)).not.toHaveBeenCalled();
});

it("clears an unsubmitted Claude response when cancelling its exact login", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  const accounts = contractAccounts();
  accounts[2].login_supported = true;
  accounts[2].auth_methods[1].capabilities.start = true;
  fixture.accounts = accounts;
  fixture.registerResult = { started: true, provider: "anthropic", account_provider: "anthropic", auth_method: "claude_pkce",
    kind: "oauth", login_id: "login:synthetic-claude", login_state: "PENDING", auth_state: "DISCONNECTED",
    catalog_state: "UNAVAILABLE", capabilities: { start: true, status: true, cancel: true, manual_complete: true } };
  fixture.cancelResult = { ...fixture.registerResult, started: false, login_state: "CANCELLED" };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "코드·리디렉션 URL 직접 입력")!.click());
  const input = row.querySelector<HTMLInputElement>('[aria-label="Claude 인증 응답"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "synthetic-secret-response");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "이 로그인 취소")!.click()); await flush();
  expect(input.value).toBe("");
  expect(row.textContent).not.toContain("synthetic-secret-response");
  expect(fixture.calls.filter(call => call.method === "model/credential/login/cancel")).toEqual([
    { method: "model/credential/login/cancel", input: { provider: "anthropic", auth_method: "claude_pkce", login_id: "login:synthetic-claude" } },
  ]);
  expect(fixture.calls.some(call => call.method === "model/credential/login/complete")).toBe(false);
});

it("keeps a completed Claude login distinct from catalog and execution readiness", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  const accounts = contractAccounts();
  accounts[2].login_supported = true;
  accounts[2].auth_methods[1].capabilities.start = true;
  fixture.accounts = accounts;
  fixture.registerResult = { started: true, provider: "anthropic", account_provider: "anthropic", auth_method: "claude_pkce",
    kind: "oauth", login_id: "login:synthetic-claude", login_state: "PENDING", auth_state: "DISCONNECTED",
    catalog_state: "UNAVAILABLE", capabilities: { start: true, status: true, cancel: true, manual_complete: true } };
  fixture.loginStatusResult = { ...fixture.registerResult, started: false, login_state: "CONNECTED", auth_state: "CONNECTED",
    catalog_state: "UNAVAILABLE", execution_eligible: false, execution_verified: false };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("로그인됐습니다. 실제 모델 응답은 첫 연구에서 확인합니다");
  expect(row.textContent).toContain("저장 인증: CONNECTED · 모델 목록: UNAVAILABLE");
  expect([...row.querySelectorAll("button")].find(button => button.textContent === "Claude 로그인됨")?.disabled).toBe(true);
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/settings/update" || call.method === "thread/start")).toBe(false);
});

it("ignores a late Codex OAuth status from the previous workspace", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = contractAccounts();
  fixture.registerResult = { started: true, provider: "codex-oauth", account_provider: "openai", auth_method: "codex_isolated_browser",
    kind: "oauth", login_id: "login:synthetic-codex", login_state: "PENDING", auth_state: "DISCONNECTED",
    catalog_state: "UNAVAILABLE", capabilities: { start: true, status: true, cancel: true, manual_complete: false } };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  fixture.holdStatus = true;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click());
  await flush();
  await vi.waitFor(() => expect(fixture.deferredStatus).not.toBeNull());
  fixture.ready = localReady({ workspace_id: WORKSPACE_B });
  await act(async () => { await client.invalidateQueries({ queryKey: ["workspace-ready"] }); });
  await flush();
  fixture.loginStatusResult = { ...fixture.registerResult, started: false, login_state: "CONNECTED", auth_state: "CONNECTED" };
  fixture.deferredStatus!(); await flush();
  expect(container.textContent).not.toContain("로그인 시도가 완료됐습니다");
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "openai")).toHaveLength(1);
});

it("uses one Codex login ID for explicit start, status and cancellation without switching the key route", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ model_connected: true });
  fixture.accounts = contractAccounts();
  fixture.registerResult = { started: true, provider: "codex-oauth", account_provider: "openai", auth_method: "codex_isolated_browser",
    route: "codex-oauth", kind: "oauth", login_id: "login:synthetic-codex", login_state: "PENDING",
    auth_state: "DISCONNECTED", catalog_state: "UNAVAILABLE",
    capabilities: { start: true, status: true, cancel: true, manual_complete: false } };
  fixture.loginStatusResult = { ...fixture.registerResult, started: false, login_state: "PENDING" };
  fixture.cancelResult = { ...fixture.registerResult, started: false, login_state: "CANCELLED" };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/register" && call.input.provider === "openai"
    && call.input.auth_method === "codex_isolated_browser")).toBe(true);
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/credential/login/status" && call.input.provider === "openai"
    && call.input.auth_method === "codex_isolated_browser" && call.input.login_id === "login:synthetic-codex")).toBe(true);
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "이 로그인 취소")!.click()); await flush();
  expect(fixture.calls.filter(call => call.method === "model/credential/login/cancel")).toEqual([
    { method: "model/credential/login/cancel", input: { provider: "openai", auth_method: "codex_isolated_browser", login_id: "login:synthetic-codex" } },
  ]);
  expect(row.textContent).toContain("기존 인증과 API 키는 유지됩니다");
  expect(row.textContent).toContain("API 키: 등록됨");
  expect(fixture.calls.some(call => call.method === "model/settings/update" || call.method === "model/credential/login/complete")).toBe(false);
});

it("does not restart an existing OAuth login to repair a missing model route", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: true, has_key: false, oauth: true,
    login_supported: true, login_kind: "codex_isolated_browser", remote_auth_verified: null, available_model_providers: ["codex-oauth"] }];
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
  fixture.registerResult = { started: false, provider: "codex-oauth", kind: "manual_device_auth", reason_code: "CODEX_DEVICE_AUTH_TERMINAL_REQUIRED",
    guidance: "Run thoth auth-connect --workspace <selected workspace> in the server terminal" };
  await mount();
  const login = [...container.querySelectorAll("button")].find(button => button.textContent === "Codex 로그인 시작")!;
  await act(async () => login.click()); await flush();
  expect(container.textContent).toContain("thoth auth-connect --workspace");
  expect(container.textContent).not.toContain("codex login --device-auth");
  expect(container.textContent).toContain("THOTH는 로그인을 시작하거나 완료하지 않았습니다");
  fixture.registerResult = { started: false, provider: "codex-oauth", kind: "manual_device_auth", guidance: "codex login --device-auth" };
  await act(async () => login.click()); await flush();
  expect(container.textContent).toContain("THOTH 전용 연결 방법을 확인하세요");
  expect(container.textContent).not.toContain("codex login --device-auth");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("keeps Codex login start and pending status separate from connection success", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.registerResult = { provider: "codex-oauth", kind: "oauth", started: true, connected: false,
    connection_state: "LOGIN_PENDING", profile_mode: "THOTH_ISOLATED", execution_verified: false };
  await mount();
  const login = [...container.querySelectorAll("button")].find(button => button.textContent === "Codex 로그인 시작")!;
  await act(async () => login.click()); await flush();
  expect(container.textContent).toContain("THOTH 전용 Codex 로그인 절차를 시작했습니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: false, has_key: false, oauth: false,
    login_supported: true, login_kind: "codex_isolated_browser", available_model_providers: [],
    connection_state: "LOGIN_PENDING", profile_mode: "THOTH_ISOLATED", execution_verified: false }];
  const refresh = [...container.querySelectorAll("button")].find(button => button.textContent === "연결 상태 다시 확인")!;
  await act(async () => refresh.click()); await flush();
  expect(container.textContent).toContain("로그인 진행 중");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "로그인 완료 대기") as HTMLButtonElement).disabled).toBe(true);
  expect(fixture.calls.filter(call => call.method === "model/credential/register")).toHaveLength(1);
});

it("does not infer login or readiness from an unrelated legacy account row", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "xai", label: "xAI", connected: true, has_key: false, oauth: true }];
  await mount();
  const xaiRow = [...container.querySelectorAll(".first-run-provider")].find(row => row.textContent?.includes("xAI"))!;
  expect(xaiRow.textContent).toContain("연결 경로 확인 필요");
  expect(xaiRow.textContent).not.toContain("로그인 상태 확인됨");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("keeps an isolated but execution-unverified account out of ready state", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: true, has_key: false, oauth: true,
    login_supported: true, login_kind: "codex_isolated_browser", available_model_providers: ["codex-oauth"],
    connection_state: "EXECUTION_UNVERIFIED", profile_mode: "THOTH_ISOLATED", remote_auth_verified: null }];
  await mount();
  expect(container.textContent).toContain("실행 미검증");
  expect(container.textContent).toContain("THOTH 전용 별도 프로필");
  expect(container.textContent).toContain("기존 Codex Desktop 인증 파일을 가져오지 않습니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
});

it("allows a locally eligible attempt while labeling actual provider success unverified", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: true, has_key: false, oauth: true,
    login_supported: true, login_kind: "codex_isolated_browser", available_model_providers: ["codex-oauth"],
    connection_state: "EXECUTION_UNVERIFIED", profile_mode: "THOTH_ISOLATED", remote_auth_verified: null,
    execution_eligible: true, execution_verified: false }];
  await mount();
  expect(container.textContent).toContain("실행 시도 가능 · 성공 미검증");
  expect(container.textContent).toContain("실제 공급자 성공은 아직 검증되지 않았습니다");
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(false);
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
});

it.each([
  { state: "CODEX_STANDALONE_NOT_INSTALLED", expected: "Codex 실행 파일 없음", reason: "CODEX_STANDALONE_NOT_INSTALLED", connected: false, oauth: false },
  { state: "CODEX_STANDALONE_PIN_UNAVAILABLE", expected: "지원 버전 없음", reason: "CODEX_STANDALONE_PIN_UNAVAILABLE", connected: false, oauth: false },
  { state: "UPDATE_REVIEW_REQUIRED", expected: "업데이트 검토 필요", reason: "UPDATE_REVIEW_REQUIRED", connected: false, oauth: false },
  { state: "LOGIN_REQUIRED", expected: "로그인 필요", reason: "CODEX_LOGIN_REQUIRED", connected: false, oauth: false },
  { state: "CATALOG_UNAVAILABLE", expected: "모델 목록 없음", reason: "MODEL_CATALOG_UNAVAILABLE", connected: true, oauth: true },
])("shows explicit $state without claiming execution readiness", async ({ state, expected, reason, connected, oauth }) => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected, has_key: false, oauth,
    login_supported: true, login_kind: "codex_isolated_browser", available_model_providers: [],
    connection_state: state, reason_code: reason, profile_mode: "THOTH_ISOLATED" }];
  await mount();
  expect(container.textContent).toContain(expected);
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
  if (state.startsWith("CODEX_STANDALONE") || state === "UPDATE_REVIEW_REQUIRED") {
    expect(([...container.querySelectorAll("button")].find(button => button.textContent === "설치·버전 확인 필요") as HTMLButtonElement).disabled).toBe(true);
  }
});

it("does not infer Codex login support from the OpenAI company name", async () => {
  fixture.ready = { ready: false, deployment_mode: "LOCAL", model_connected: false, setup: { internet_consent: "UNDECIDED" } };
  fixture.accounts = [{ provider: "openai", label: "ChatGPT", connected: false, has_key: false, oauth: false }];
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  const unknown = [...row.querySelectorAll("button")].find(button => button.textContent === "연결 방법 확인 중") as HTMLButtonElement;
  expect(unknown.disabled).toBe(true);
});

it("names a missing pinned Codex tool and offers its install command without retrying the login", async () => {
  // Reproduces the first-run failure seen on a fresh PC: the server rejected the start with a
  // typed reason, but the screen showed only a generic "could not confirm" message.
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = contractAccounts();
  fixture.registerError = "RPC:CODEX_STANDALONE_PIN_UNAVAILABLE";
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  expect(row.textContent).toContain("Codex 연결 도구(0.157.1)를 찾지 못했습니다");
  expect(row.textContent).toContain("@openai/codex@0.157.1");
  expect(row.textContent).toContain("오류 코드: CODEX_STANDALONE_PIN_UNAVAILABLE");
  expect(row.textContent).not.toContain("로그인 시작 결과를 확인하지 못했습니다");
  expect(fixture.calls.filter(call => call.method === "model/credential/register" && call.input.provider === "openai")).toHaveLength(1);
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
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
  const keyButton = [...xaiRow.querySelectorAll("button")].find(button => button.textContent === "API 키로 연결")!;
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
    login_supported: true, login_kind: "codex_isolated_browser", remote_auth_verified: null, available_model_providers: ["openai"],
    connection_state: "API_KEY_AVAILABLE", profile_mode: "THOTH_LOCAL_KEY", execution_eligible: true, execution_verified: false }];
  await mount(true);
  for (let i = 0; i < 12 && ![...container.querySelectorAll("button")].some(button => button.textContent === "다음" && !button.disabled); i++) await flush();
  expect(container.querySelectorAll("main")).toHaveLength(1);
  expect(container.querySelector(".first-run-shell")?.closest(".conversation-workstation")).toBeNull();
  const next = [...container.querySelectorAll("button")].find(button => button.textContent === "다음");
  expect(next?.disabled, container.textContent ?? "").toBe(false);
  await act(async () => next!.click()); await flush();
  expect(container.textContent).toContain("2 / 2");
  expect(container.querySelectorAll("main")).toHaveLength(1);
  expect(container.querySelector(".conversation-workstation")).toBeNull();
  const deny = [...container.querySelectorAll("button")].find(button => button.textContent?.includes("거부"));
  await act(async () => deny!.click()); await flush();
  await flush();
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

function connectedCodexLogin() {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = contractAccounts();
  fixture.registerResult = { started: true, provider: "codex-oauth", account_provider: "openai", auth_method: "codex_isolated_browser",
    route: "codex-oauth", kind: "oauth", login_id: "login:synthetic-codex", login_state: "PENDING",
    auth_state: "DISCONNECTED", catalog_state: "UNAVAILABLE",
    capabilities: { start: true, status: true, cancel: true, manual_complete: false } };
  fixture.loginStatusResult = { ...fixture.registerResult, started: false, login_state: "CONNECTED", auth_state: "CONNECTED" };
}
const refreshCalls = () => fixture.calls.filter(call => call.method === "model/catalog/refresh");

it("loads the model list exactly once when a login first reports connected", async () => {
  connectedCodexLogin();
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  expect(refreshCalls()).toHaveLength(0);
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(refreshCalls()).toEqual([{ method: "model/catalog/refresh", input: { project_id: "system:workspace" } }]);
  expect(row.textContent).toContain("로그인됐습니다");
  await flush(); await flush();
  expect(refreshCalls()).toHaveLength(1);
  expect(fixture.calls.filter(call => call.method === "model/credential/login/status")).toHaveLength(1);
});

it("keeps a completed login and names the model-list failure when the refresh fails", async () => {
  connectedCodexLogin();
  fixture.refreshError = "RPC:MODEL_CATALOG_REFRESH_TIMEOUT";
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("로그인됐습니다");
  expect(row.textContent).toContain("모델 목록을 불러오지 못했습니다");
  expect(refreshCalls()).toHaveLength(1);
  expect(([...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement).disabled).toBe(true);
});

it("offers a load-models button for a login without a model list and enables next after one refresh", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady({ model_connected: false });
  const waiting = { provider: "openai", label: "ChatGPT", connected: true, has_key: false, oauth: true,
    login_supported: true, login_kind: "codex_isolated_browser", available_model_providers: [] as string[],
    connection_state: "CATALOG_UNAVAILABLE", reason_code: "CATALOG_UNAVAILABLE", profile_mode: "THOTH_ISOLATED",
    execution_eligible: false, execution_verified: false, remote_auth_verified: null };
  fixture.accounts = [waiting];
  fixture.refreshEffect = () => {
    fixture.ready = { ...fixture.ready, model_connected: true };
    fixture.accounts = [{ ...waiting, available_model_providers: ["codex-oauth"],
      connection_state: "EXECUTION_UNVERIFIED", execution_eligible: true }];
  };
  await mount();
  const next = () => [...container.querySelectorAll("button")].find(button => button.textContent === "다음") as HTMLButtonElement;
  expect(container.textContent).toContain("모델 확인 필요");
  expect(next().disabled).toBe(true);
  expect(refreshCalls()).toHaveLength(0);
  const recheck = [...container.querySelectorAll("button")].find(button => button.textContent === "연결 상태 다시 확인")!;
  await act(async () => recheck.click()); await flush();
  expect(refreshCalls()).toHaveLength(0);
  const load = [...container.querySelectorAll("button")].find(button => button.textContent === "모델 목록 불러오기")!;
  await act(async () => load.click()); await flush();
  expect(refreshCalls()).toEqual([{ method: "model/catalog/refresh", input: { project_id: "system:workspace" } }]);
  expect(next().disabled).toBe(false);
  expect([...container.querySelectorAll("button")].some(button => button.textContent === "모델 목록 불러오기")).toBe(false);
  expect(fixture.calls.some(call => call.method === "model/settings/update" || call.method === "model/credential/register")).toBe(false);
});

function claudeCodeAccounts(state: { connection_state: string; reason_code: string; start: boolean; connected?: boolean }) {
  const accounts: Record<string, unknown>[] = defaultAccounts();
  accounts[1] = { provider: "anthropic", label: "Claude", connected: Boolean(state.connected), has_key: false, oauth: Boolean(state.connected),
    login_supported: state.start, login_kind: state.start ? "claude_code_login" : "unsupported", remote_auth_verified: null,
    available_model_providers: state.connected ? ["claude-code"] : [], connection_state: state.connection_state,
    reason_code: state.reason_code, execution_eligible: Boolean(state.connected), execution_verified: false,
    auth_methods: [
      { auth_method: "api_key", route: "anthropic", connected: false, execution_eligible: false, connection_state: "LOGIN_REQUIRED",
        reason_code: "MODEL_CREDENTIAL_UNAVAILABLE", capabilities: { start: false, status: true, cancel: false, manual_complete: false } },
      { auth_method: "claude_code_login", route: "claude-code", connected: Boolean(state.connected), execution_eligible: Boolean(state.connected),
        connection_state: state.connection_state, reason_code: state.reason_code,
        capabilities: { start: state.start, status: true, cancel: true, manual_complete: true } },
    ] };
  return accounts as unknown as typeof fixture.accounts;
}
const claudeCodeStart = (url: string | undefined = "https://claude.ai/oauth/authorize?state=synthetic-state") => ({
  started: true, provider: "claude-code", account_provider: "anthropic", auth_method: "claude_code_login", route: "claude-code",
  kind: "claude_code_login", login_id: "login:synthetic-claude-code", login_state: "PENDING", auth_state: "DISCONNECTED",
  catalog_state: "UNAVAILABLE", expires_at: Math.floor(Date.now() / 1000) + 600, authorization_url: url,
  capabilities: { start: true, status: true, cancel: true, manual_complete: true } });
const claudeRow = () => [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("Claude"))!;

it("signs in with Claude through the official executable and loads the model list once", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = claudeCodeAccounts({ connection_state: "LOGIN_REQUIRED", reason_code: "CLAUDE_CODE_LOGIN_REQUIRED", start: true });
  fixture.registerResult = claudeCodeStart();
  fixture.loginStatusResult = { ...claudeCodeStart(undefined), started: false, login_state: "CONNECTED", auth_state: "CONNECTED", catalog_state: "AVAILABLE" };
  await mount();
  const row = claudeRow();
  expect(row.textContent).toContain("Claude 계정으로 로그인하거나 Anthropic API 키로 연결합니다");
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "Claude Code 연결 안내")).toBe(false);
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  expect(fixture.calls.filter(call => call.method === "model/credential/register")).toEqual([
    { method: "model/credential/register", input: { project_id: "system:workspace", provider: "anthropic", auth_method: "claude_code_login" } },
  ]);
  expect(row.querySelector('a[href^="https://claude.ai/oauth/authorize"]')).not.toBeNull();
  expect(refreshCalls()).toHaveLength(0);
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("로그인됐습니다");
  expect(refreshCalls()).toHaveLength(1);
  expect(fixture.calls.some(call => call.method === "model/settings/update" || call.method === "thread/start")).toBe(false);
});

it("accepts a claude.com login link and refuses any other host", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = claudeCodeAccounts({ connection_state: "LOGIN_REQUIRED", reason_code: "CLAUDE_CODE_LOGIN_REQUIRED", start: true });
  fixture.registerResult = claudeCodeStart("https://claude.com/cai/oauth/authorize?state=synthetic-state");
  await mount();
  await act(async () => [...claudeRow().querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  expect(claudeRow().querySelector('a[href^="https://claude.com/cai/oauth/authorize"]')).not.toBeNull();
  await act(async () => [...claudeRow().querySelectorAll("button")].find(button => button.textContent === "이 로그인 취소")!.click()); await flush();
  fixture.registerResult = claudeCodeStart("https://claude.ai.evil.example/oauth/authorize");
  await act(async () => [...claudeRow().querySelectorAll("button")].find(button => button.textContent === "Claude로 로그인")!.click()); await flush();
  expect(claudeRow().querySelector("a[href]")).toBeNull();
});

it("explains a missing Claude Code with the THOTH-only folder install, never a global install", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = claudeCodeAccounts({ connection_state: "CLAUDE_CODE_NOT_INSTALLED", reason_code: "CLAUDE_CODE_NOT_INSTALLED", start: false });
  await mount();
  const row = claudeRow();
  expect(row.textContent).toContain("도구 설치 필요");
  expect(row.textContent).toContain("Claude Code가 이 PC에 없습니다");
  expect(row.textContent).toContain("@anthropic-ai/claude-code@");
  expect(row.textContent).toContain("THOTH\\tools\\claude-code");
  expect(row.textContent).not.toContain("install -g");
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "Claude로 로그인")).toBe(false);
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
});

const installCalls = () => fixture.calls.filter(call => call.method === "model/tooling/install");
const chatgptRow = () => [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
const buttonNamed = (scope: Element, label: string) => [...scope.querySelectorAll("button")].find(button => button.textContent === label) as HTMLButtonElement | undefined;

it("installs the Codex tool from the missing-tool help, then lets the login be retried", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = contractAccounts();
  fixture.registerError = "RPC:CODEX_STANDALONE_PIN_UNAVAILABLE";
  await mount();
  await act(async () => buttonNamed(chatgptRow(), "ChatGPT로 로그인")!.click()); await flush();
  expect(chatgptRow().textContent).toContain("@openai/codex@0.157.1");
  fixture.registerError = null;
  fixture.installEffect = () => { fixture.ready = { ...fixture.ready }; };
  await act(async () => buttonNamed(chatgptRow(), "자동 설치")!.click()); await flush();
  expect(installCalls()).toEqual([{ method: "model/tooling/install", input: { project_id: "system:workspace", tool_id: "codex" } }]);
  expect(chatgptRow().textContent).not.toContain("오류 코드: CODEX_STANDALONE_PIN_UNAVAILABLE");
  expect(chatgptRow().textContent).toContain("설치했습니다");
  expect(fixture.calls.filter(call => call.method === "model/credential/register")).toHaveLength(1);
});

it("installs Claude Code into the THOTH folder and then offers the login button", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = claudeCodeAccounts({ connection_state: "CLAUDE_CODE_NOT_INSTALLED", reason_code: "CLAUDE_CODE_NOT_INSTALLED", start: false });
  fixture.installEffect = () => {
    fixture.accounts = claudeCodeAccounts({ connection_state: "LOGIN_REQUIRED", reason_code: "CLAUDE_CODE_LOGIN_REQUIRED", start: true });
  };
  await mount();
  expect(buttonNamed(claudeRow(), "Claude로 로그인")).toBeUndefined();
  await act(async () => buttonNamed(claudeRow(), "자동 설치")!.click()); await flush();
  expect(installCalls()).toEqual([{ method: "model/tooling/install", input: { project_id: "system:workspace", tool_id: "claude-code" } }]);
  expect(buttonNamed(claudeRow(), "Claude로 로그인")).toBeDefined();
  expect(claudeRow().textContent).not.toContain("@anthropic-ai/claude-code@");
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
});

it("keeps the copy-command fallback and names the reason when the automatic install fails", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = claudeCodeAccounts({ connection_state: "CLAUDE_CODE_NOT_INSTALLED", reason_code: "CLAUDE_CODE_NOT_INSTALLED", start: false });
  fixture.installError = "RPC:NODE_NPM_UNAVAILABLE";
  await mount();
  await act(async () => buttonNamed(claudeRow(), "자동 설치")!.click()); await flush();
  expect(claudeRow().textContent).toContain("Node.js");
  expect(claudeRow().textContent).toContain("NODE_NPM_UNAVAILABLE");
  expect(claudeRow().textContent).toContain("@anthropic-ai/claude-code@2.1.284");
  expect(buttonNamed(claudeRow(), "명령 복사")).toBeDefined();
  expect(installCalls()).toHaveLength(1);
});

it("does not start a second install while one is running", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  fixture.accounts = claudeCodeAccounts({ connection_state: "CLAUDE_CODE_NOT_INSTALLED", reason_code: "CLAUDE_CODE_NOT_INSTALLED", start: false });
  let release = () => undefined as void;
  fixture.installGate = new Promise<void>(resolve => { release = resolve; });
  await mount();
  await act(async () => buttonNamed(claudeRow(), "자동 설치")!.click()); await flush();
  const running = claudeRow().querySelector("button[disabled], button.bp6-disabled");
  expect(running).not.toBeNull();
  expect(claudeRow().textContent).toContain("설치 중");
  await act(async () => { [...claudeRow().querySelectorAll("button")].forEach(button => button.click()); }); await flush();
  expect(installCalls()).toHaveLength(1);
  await act(async () => release()); await flush();
});

it("shows when each list was last checked after the load-models button, and a failed refresh keeps the last list", async () => {
  fixture.ready = localReady({ model_connected: false });
  const waiting = { provider: "openai", label: "ChatGPT", connected: true, has_key: false, oauth: true,
    login_supported: true, login_kind: "codex_isolated_browser", available_model_providers: [] as string[],
    connection_state: "CATALOG_UNAVAILABLE", reason_code: "CATALOG_UNAVAILABLE", profile_mode: "THOTH_ISOLATED",
    execution_eligible: false, execution_verified: false, remote_auth_verified: null };
  fixture.accounts = [waiting];
  fixture.catalogStatus = [{ provider: "codex-oauth", source: "PROVIDER_LIST", status: "STALE_LAST_GOOD", fetched_at: new Date().toISOString(),
    failure_reason: "CATALOG_UNAVAILABLE", excluded: [] }];
  await mount();
  const load = [...container.querySelectorAll("button")].find(button => button.textContent === "모델 목록 불러오기")!;
  await act(async () => load.click()); await flush();
  const result = container.querySelector("[data-catalog-result]")?.textContent ?? "";
  expect(result).toContain("마지막 확인: 오늘");
  expect(result).toContain("갱신 실패");
  expect(result).toContain("마지막으로 확인한 목록을 보여 줍니다");
  expect(container.textContent).not.toContain("STALE_LAST_GOOD");
});

it("does not load the model list again when the server already did it after the login finished", async () => {
  connectedCodexLogin();
  fixture.loginStatusResult = { ...fixture.loginStatusResult, catalog_refresh: "DONE", catalog_state: "AVAILABLE" };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("로그인됐습니다");
  expect(refreshCalls()).toHaveLength(0);
  expect(row.textContent).not.toContain("모델 목록을 불러오지 못했습니다");
});

it("says the list failed when the server tried after the login and could not load it, without asking again", async () => {
  connectedCodexLogin();
  fixture.loginStatusResult = { ...fixture.loginStatusResult, catalog_refresh: "FAILED", catalog_state: "UNAVAILABLE" };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("로그인됐습니다");
  expect(row.textContent).toContain("모델 목록을 불러오지 못했습니다");
  expect(refreshCalls()).toHaveLength(0);
});

it("closes the progress panel once the account is connected, even while the server loads the list", async () => {
  connectedCodexLogin();
  fixture.loginStatusResult = { ...fixture.loginStatusResult, login_state: "PENDING", auth_state: "CONNECTED", catalog_refresh: "RUNNING" };
  await mount();
  const row = [...container.querySelectorAll(".first-run-provider")].find(item => item.textContent?.includes("ChatGPT"))!;
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "ChatGPT로 로그인")!.click()); await flush();
  expect(row.textContent).toContain("남은 상태 확인 시간");
  await act(async () => [...row.querySelectorAll("button")].find(button => button.textContent === "상태 직접 확인")!.click()); await flush();
  expect(row.textContent).toContain("로그인이 확인됐습니다. 모델 목록을 받는 중입니다");
  expect(row.textContent).not.toContain("남은 상태 확인 시간");
  expect([...row.querySelectorAll("button")].some(button => button.textContent === "이 로그인 취소")).toBe(false);
  expect(refreshCalls()).toHaveLength(0);
});

it("shows the server's English guidance in Korean and keeps unknown English out of the notes", async () => {
  fixture.forbidNetwork = true;
  fixture.ready = localReady();
  const account = { ...contractAccounts()[0], guidance: "The Codex route is eligible; live execution has not been verified" };
  fixture.accounts = [account, { ...contractAccounts()[1], guidance: "Sign in with the official Claude Code executable in the THOTH profile" },
    { ...contractAccounts()[2], guidance: "Some brand new English hint from a newer server" }];
  await mount("panel");
  // The original English stays available, but only inside the collapsed technical details.
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("details").forEach(item => item.remove());
  const text = clone.textContent ?? "";
  expect(text).toContain("Codex 경로는 사용을 시도할 수 있지만 실제 실행은 아직 확인되지 않았습니다");
  expect(text).toContain("공식 Claude Code로 로그인하세요");
  expect(text).not.toContain("The Codex route is eligible");
  expect(text).not.toContain("Sign in with the official Claude Code");
  expect(text).not.toContain("Some brand new English hint");
});

