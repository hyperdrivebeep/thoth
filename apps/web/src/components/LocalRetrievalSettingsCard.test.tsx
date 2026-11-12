// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  LocalAssetRead,
  OperationTimeouts,
  RetrievalExperimentRead,
} from "../api/localRetrieval";
import { LOCAL_RETRIEVAL_INSTALL_COMMAND } from "../api/localRetrievalInstall";
import { setLocale } from "../i18n/locale";
import { localRetrievalReason, localRetrievalText } from "../i18n/localRetrievalMessages";
import { LocalRetrievalSettingsCard } from "./LocalRetrievalSettingsCard";

/* Reads go through the shared rpc(); long actions go through fetch. Both are synthetic here: no server, no model. */
const fixture = vi.hoisted(() => ({
  calls: [] as { method: string; input: Record<string, unknown> }[],
  asset: (() => {
    throw new Error("ASSET_FIXTURE_UNSET");
  }) as () => LocalAssetRead,
  project: (() => {
    throw new Error("PROJECT_FIXTURE_UNSET");
  }) as (id: string) => RetrievalExperimentRead,
  configure: (() => {
    throw new Error("CONFIGURE_FIXTURE_UNSET");
  }) as (input: Record<string, unknown>) => unknown,
}));
vi.mock("../api/rpcClient", () => ({
  rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    if (method === "model/localAsset/read")
      return { operation_id: "read", state: "SUCCEEDED", value: fixture.asset() };
    if (method === "project/retrieval/experiment/read")
      return {
        operation_id: "read",
        state: "SUCCEEDED",
        value: fixture.project(String(input.project_id)),
      };
    if (method === "project/retrieval/experiment/configure") return fixture.configure(input);
    throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
  },
}));

type Script = {
  state: "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  result?: unknown;
  error?: unknown;
};
type Start = {
  method: string;
  input: Record<string, unknown>;
  idempotencyKey: string;
  operationId: string;
};

/** Resolves with the work, or rejects like a real fetch when the request's signal is aborted first. */
function untilAbort<T>(work: Promise<T>, signal: AbortSignal | null | undefined): Promise<T> {
  if (!signal) return work;
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new DOMException("The operation was aborted.", "AbortError"));
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    work.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

/** A response whose headers arrived but whose JSON body only arrives after `bodyHold` (or never, if aborted first). */
function stalledBody(
  payload: unknown,
  status: number,
  bodyHold: Promise<void>,
  signal: AbortSignal | null | undefined,
): Response {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    json: () =>
      untilAbort(
        bodyHold.then(() => payload),
        signal,
      ),
  };
  return response as unknown as Response;
}

/** The existing /rpc/async, /operations/{id} and /operations/{id}/cancel routes, scripted per operation. */
class FakeServer {
  starts: Start[] = [];
  polls: string[] = [];
  cancels: { operationId: string; projectId: string }[] = [];
  status = new Map<string, Script>();
  /** Result reads wait here (the server is slow to answer). */
  hold: Promise<void> | null = null;
  /** The start request is received and recorded, but its answer waits here. */
  startHold: Promise<void> | null = null;
  /** Headers of the start answer arrive, but its JSON body waits here. */
  startBodyHold: Promise<void> | null = null;
  /** Headers of a result read arrive, but its JSON body waits here. */
  pollBodyHold: Promise<void> | null = null;
  /** 200 starts work; 4xx answers a JSON-RPC refusal; 408/429/5xx answer a non-envelope error page. */
  startStatus = 200;
  private next = 1;
  private answer(
    payload: unknown,
    status: number,
    bodyHold: Promise<void> | null,
    signal: AbortSignal | null | undefined,
  ): Response {
    if (bodyHold) return stalledBody(payload, status, bodyHold, signal);
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json" },
    });
  }
  fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
    if (url === "/rpc/async") {
      const body = JSON.parse(String(init?.body)) as {
        id: string;
        method: string;
        params: { _meta: { idempotencyKey: string }; input: Record<string, unknown> };
      };
      const key = body.params._meta.idempotencyKey;
      const operationId =
        this.starts.find((start) => start.idempotencyKey === key)?.operationId ??
        `op:${this.next++}`;
      this.starts.push({
        method: body.method,
        input: body.params.input,
        idempotencyKey: key,
        operationId,
      });
      if (this.startHold) await untilAbort(this.startHold, init?.signal);
      if (this.startStatus === 408 || this.startStatus === 429 || this.startStatus >= 500) {
        return this.answer(
          { detail: "SYNTHETIC_UNAVAILABLE" },
          this.startStatus,
          null,
          init?.signal,
        );
      }
      if (this.startStatus !== 200) {
        return this.answer(
          {
            jsonrpc: "2.0",
            id: body.id,
            error: {
              code: -32040,
              message: "LOCAL_RETRIEVAL_SCOPE_DENIED",
              data: { reason_code: "LOCAL_RETRIEVAL_SCOPE_DENIED" },
            },
          },
          this.startStatus,
          null,
          init?.signal,
        );
      }
      if (!this.status.has(operationId)) this.status.set(operationId, { state: "RUNNING" });
      return this.answer(
        {
          jsonrpc: "2.0",
          id: body.id,
          result: {
            operation_id: operationId,
            state: "RUNNING",
            value: { status: "ACCEPTED_RUNNING" },
          },
        },
        200,
        this.startBodyHold,
        init?.signal,
      );
    }
    const cancel = /^\/operations\/([^/?]+)\/cancel$/.exec(url);
    if (cancel) {
      const operationId = decodeURIComponent(cancel[1]);
      this.cancels.push({
        operationId,
        projectId: String((JSON.parse(String(init?.body)) as { project_id: string }).project_id),
      });
      return Response.json({
        jsonrpc: "2.0",
        id: "cancel",
        result: { operation_id: operationId, state: "CANCEL_REQUESTED", value: {} },
      });
    }
    const poll = /^\/operations\/([^/?]+)\?project_id=(.+)$/.exec(url);
    if (poll) {
      const operationId = decodeURIComponent(poll[1]);
      this.polls.push(operationId);
      if (this.hold) await untilAbort(this.hold, init?.signal);
      const script = this.status.get(operationId) ?? {
        state: "FAILED",
        error: { message: "UNKNOWN_OPERATION" },
      };
      return this.answer(
        {
          operation_id: operationId,
          state: script.state,
          result: script.result ?? null,
          error: script.error ?? null,
        },
        200,
        this.pollBodyHold,
        init?.signal,
      );
    }
    throw new Error(`UNEXPECTED_FETCH:${url}`);
  });
}

const FAST: Partial<OperationTimeouts> = {
  startMs: 500,
  pollMs: 500,
  pollIntervalMs: 5,
  totalMs: 2000,
  cancelMs: 500,
};
const ko = (key: Parameters<typeof localRetrievalText>[0]) => localRetrievalText(key, "ko");
const en = (key: Parameters<typeof localRetrievalText>[0]) => localRetrievalText(key, "en");

const assetReady = (): LocalAssetRead => ({
  schema_version: "1.0.0",
  supported: true,
  can_manage: true,
  model_name: "ibm-granite/granite-embedding-97m-multilingual-r2",
  manifest_digest: "digest:1",
  total_bytes: 123_600_000,
  asset_state: "READY",
  dependency_available: true,
  worker_loaded: false,
  reason_code: null,
  active: null,
});
const assetMissing = (): LocalAssetRead => ({ ...assetReady(), asset_state: "MODEL_REQUIRED" });
const projectOff = (id = "p1"): RetrievalExperimentRead => ({
  project_id: id,
  can_prepare: true,
  enabled: false,
  generation: 0,
  state: "OFF",
  ready_sources: 0,
  total_sources: 0,
  pending_sources: 0,
  failed_sources: 0,
  jobs: [],
  active_operation_id: null,
});
const projectEnabled = (id = "p1"): RetrievalExperimentRead => ({
  ...projectOff(id),
  enabled: true,
  generation: 1,
  state: "INDEX_PENDING",
  total_sources: 2,
  pending_sources: 2,
});

let root: Root, container: HTMLDivElement, client: QueryClient, server: FakeServer;
const tick = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));
const settle = async (ms?: number) => {
  await act(async () => {
    await tick(ms);
  });
};
type Props = {
  workspaceId?: string;
  projectId?: string;
  hosted?: boolean;
  timeouts?: Partial<OperationTimeouts>;
  part?: "all" | "computer" | "project";
};
const body = () => container.textContent ?? "";
/** Queries start in effects after the commit, so the initial reads are awaited outside the render act. */
async function render({ workspaceId = "workspace:local", timeouts = FAST, ...rest }: Props = {}) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <LocalRetrievalSettingsCard workspaceId={workspaceId} timeouts={timeouts} {...rest} />
      </QueryClientProvider>,
    );
  });
  await settle();
  if (rest.hosted) return;
  const projectLoaded = () =>
    !rest.projectId ||
    document.getElementById("local-retrieval-enable") !== null ||
    body().includes(ko("localRetrieval.readFailed"));
  await until(() => !body().includes("…") && projectLoaded());
}
const buttons = () => [...container.querySelectorAll("button")];
const button = (label: string) => {
  const found = buttons().find((element) => element.textContent?.trim() === label);
  if (!found) throw new Error(`BUTTON_NOT_FOUND:${label}`);
  return found;
};
const hasButton = (label: string) =>
  buttons().some((element) => element.textContent?.trim() === label);
const isDisabled = (element: HTMLElement) =>
  (element as HTMLButtonElement).disabled ||
  element.getAttribute("aria-disabled") === "true" ||
  element.classList.contains("bp6-disabled");
const inputById = (id: string) => document.getElementById(id) as HTMLInputElement | null;
/** The event runs inside act; the work it starts (mutations, fetches, state from effects) settles after it. */
async function click(element: HTMLElement) {
  await act(async () => {
    element.click();
  });
  await settle();
}
async function typeInto(id: string, value: string) {
  const element = inputById(id);
  if (!element) throw new Error(`INPUT_NOT_FOUND:${id}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
}
const reads = (method: string) => fixture.calls.filter((call) => call.method === method);
/** Invalidates (optionally one key) and waits for the refetch and its render. */
async function refetch(queryKey?: unknown[]) {
  await act(async () => {
    await client.invalidateQueries(queryKey ? { queryKey } : undefined);
  });
  await settle();
}
const operationState = () =>
  container.querySelector("[data-operation-state]")?.getAttribute("data-operation-state") ?? null;
async function until(condition: () => boolean, limitMs = 1500) {
  const started = Date.now();
  while (!condition()) {
    if (Date.now() - started > limitMs) throw new Error("CONDITION_TIMEOUT");
    await settle(10);
  }
}

/** Unknown starts must resend the original request; known operations use GET-only tests below. */
async function confirmUnknownStart() {
  expect(server.starts).toHaveLength(1);
  const first = server.starts[0];
  await click(button(ko("localRetrieval.confirmResultButton")));
  await until(() => operationState() === "succeeded");
  expect(server.starts).toHaveLength(2);
  expect(server.starts[1].idempotencyKey).toBe(first.idempotencyKey);
  expect(server.starts[1].operationId).toBe(first.operationId);
  expect(server.starts[1].method).toBe(first.method);
  expect(server.starts[1].input).toEqual(first.input);
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  setLocale("ko");
  fixture.calls.length = 0;
  fixture.asset = assetReady;
  fixture.project = projectOff;
  fixture.configure = () => {
    throw new Error("CONFIGURE_NOT_EXPECTED");
  };
  server = new FakeServer();
  vi.stubGlobal("fetch", server.fetch);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(async () => {
  await act(async () => {
    root.unmount();
    await tick();
  });
  client.clear();
  container.remove();
  vi.unstubAllGlobals();
  setLocale("ko");
});

describe("hosted and default state", () => {
  it("isolates an invalid projection as a read error instead of crashing settings", async () => {
    fixture.project = () => ({}) as RetrievalExperimentRead;
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.readFailed"));
    expect(body()).toContain(ko("localRetrieval.title"));
    expect(server.starts).toHaveLength(0);
  });

  it("rejects another project's projection", async () => {
    fixture.project = () => projectEnabled("p2");
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.readFailed"));
    expect(hasButton(ko("localRetrieval.prepareButton"))).toBe(false);
  });
  it("hosted workspace: local-only notice, no RPC and no fetch", async () => {
    await render({ hosted: true, projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.hostedOnly"));
    expect(fixture.calls).toEqual([]);
    expect(server.fetch).not.toHaveBeenCalled();
    expect(inputById("local-retrieval-enable")).toBeNull();
  });

  it("default is off: only reads happen, nothing starts on its own", async () => {
    await render({ projectId: "p1" });
    expect(new Set(fixture.calls.map((call) => call.method))).toEqual(
      new Set(["model/localAsset/read", "project/retrieval/experiment/read"]),
    );
    expect(server.fetch).not.toHaveBeenCalled();
    expect(body()).toContain(ko("localRetrieval.index.OFF"));
    expect(body()).toContain(ko("localRetrieval.readyNote"));
    expect(body()).toContain("123.6 MB · 123,600,000 바이트");
    expect(inputById("local-retrieval-enable")?.checked).toBe(false);
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(true);
    expect(body()).toContain(ko("localRetrieval.prepareBlockedOff"));
    expect(operationState()).toBeNull();
  });

  it("without a project only the workspace model section is shown", async () => {
    await render();
    expect(body()).toContain(ko("localRetrieval.noProject"));
    expect(fixture.calls.map((call) => call.method)).toEqual(["model/localAsset/read"]);
  });

  it("tells the person to rebuild an index made under an earlier preprocessing version, in every language", async () => {
    fixture.project = () => ({ ...projectEnabled(), outdated_index: true });
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.outdatedIndex"));
    for (const locale of ["en", "zh-Hans", "ja"] as const) {
      await act(async () => {
        setLocale(locale);
        await tick();
      });
      expect(body()).toContain(localRetrievalText("localRetrieval.outdatedIndex", locale));
    }
    expect(localRetrievalText("localRetrieval.outdatedIndex", "ja")).not.toBe(
      ko("localRetrieval.outdatedIndex"),
    );
  });

  it("shows no rebuild line when the index is current or the server does not say", async () => {
    fixture.project = projectEnabled;
    await render({ projectId: "p1" });
    expect(body()).not.toContain(ko("localRetrieval.outdatedIndex"));
    await act(async () => {
      root.unmount();
      await tick();
    });
    root = createRoot(container);
    fixture.project = () => ({ ...projectEnabled(), outdated_index: false });
    await render({ projectId: "p1" });
    expect(body()).not.toContain(ko("localRetrieval.outdatedIndex"));
  });

  it("switches wording between Korean and English without re-reading", async () => {
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.title"));
    const before = fixture.calls.length;
    await act(async () => {
      setLocale("en");
      await tick();
    });
    expect(body()).toContain(en("localRetrieval.title"));
    expect(body()).toContain(en("localRetrieval.index.OFF"));
    expect(body()).not.toContain(ko("localRetrieval.title"));
    expect(body()).toContain("123.6 MB · 123,600,000 bytes");
    expect(fixture.calls.length).toBe(before);
  });
});

describe("model files", () => {
  it("install needs the download confirmation, which resets when the manifest changes", async () => {
    fixture.asset = assetMissing;
    await render();
    expect(body()).toContain(ko("localRetrieval.asset.MODEL_REQUIRED"));
    expect(body()).toContain(ko("localRetrieval.installNotice"));
    const confirm = inputById("local-retrieval-install-confirm")!;
    expect(confirm.checked).toBe(false);
    expect(isDisabled(button(ko("localRetrieval.installButton")))).toBe(true);
    await click(confirm);
    expect(inputById("local-retrieval-install-confirm")!.checked).toBe(true);
    expect(isDisabled(button(ko("localRetrieval.installButton")))).toBe(false);
    fixture.asset = () => ({ ...assetMissing(), manifest_digest: "digest:2" });
    await refetch(["local-retrieval-asset", "workspace:local"]);
    expect(inputById("local-retrieval-install-confirm")!.checked).toBe(false);
    expect(isDisabled(button(ko("localRetrieval.installButton")))).toBe(true);
    expect(server.fetch).not.toHaveBeenCalled();
    await click(inputById("local-retrieval-install-confirm")!);
    server.status.set("op:1", { state: "SUCCEEDED", result: { asset_state: "READY" } });
    const readsBeforeInstall = reads("model/localAsset/read").length;
    await click(button(ko("localRetrieval.installButton")));
    await until(() => operationState() === "succeeded");
    expect(server.starts).toHaveLength(1);
    expect(server.starts[0].method).toBe("model/localAsset/install");
    expect(server.starts[0].input).toEqual({
      project_id: "system:workspace",
      manifest_digest: "digest:2",
      confirm_download: true,
    });
    expect(server.starts[0].idempotencyKey.length).toBeGreaterThan(0);
    expect(body()).toContain(ko("localRetrieval.opSucceeded"));
    expect(reads("model/localAsset/read").length).toBeGreaterThan(readsBeforeInstall);
  });

  it("imports an explicitly entered local folder through the async route", async () => {
    fixture.asset = assetMissing;
    await render();
    const importButton = () => button(ko("localRetrieval.importButton"));
    expect(isDisabled(importButton())).toBe(true);
    await typeInto("local-retrieval-source-path", "/models/granite-copy");
    expect(isDisabled(importButton())).toBe(false);
    server.status.set("op:1", { state: "SUCCEEDED", result: { asset_state: "READY" } });
    await click(importButton());
    await until(() => operationState() === "succeeded");
    expect(server.starts).toHaveLength(1);
    expect(server.starts[0].method).toBe("model/localAsset/import");
    expect(server.starts[0].input).toEqual({
      project_id: "system:workspace",
      source_path: "/models/granite-copy",
      manifest_digest: "digest:1",
    });
    expect(server.polls).toContain("op:1");
    expect(server.fetch.mock.calls[0][0]).toBe("/rpc/async");
  });

  it("shows a translated reason and no raw text when the server refuses to start", async () => {
    fixture.asset = assetMissing;
    server.startStatus = 403;
    await render();
    await typeInto("local-retrieval-source-path", "/models/granite-copy");
    await click(button(ko("localRetrieval.importButton")));
    await until(() => operationState() === "rejected");
    expect(body()).toContain(ko("localRetrieval.opRejected"));
    expect(body()).toContain(localRetrievalReason("LOCAL_RETRIEVAL_SCOPE_DENIED", "ko"));
    expect(body()).not.toContain("SYNTHETIC");
    expect(hasButton(ko("localRetrieval.confirmResultButton"))).toBe(false);
    expect(server.polls).toEqual([]);
  });

  it("without manage permission no import or install controls appear; without the runtime component only indexing is blocked", async () => {
    fixture.asset = () => ({ ...assetMissing(), can_manage: false });
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.manageDenied"));
    expect(inputById("local-retrieval-source-path")).toBeNull();
    expect(hasButton(ko("localRetrieval.installButton"))).toBe(false);

    fixture.asset = () => ({ ...assetMissing(), dependency_available: false });
    fixture.project = projectEnabled;
    await refetch();
    expect(inputById("local-retrieval-source-path")).not.toBeNull();
    expect(body()).toContain(ko("localRetrieval.dependencyMissing"));
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(true);
    expect(body()).toContain(ko("localRetrieval.prepareBlockedAsset"));

    fixture.asset = () => ({ ...assetReady(), dependency_available: false });
    await refetch();
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(true);
    expect(body()).toContain(ko("localRetrieval.prepareBlockedDependency"));
    expect(server.fetch).not.toHaveBeenCalled();
  });
});

describe("runtime component install guide", () => {
  const command = () => container.querySelector(".local-retrieval-command");

  it("without the runtime component shows the command and the restart note, under the existing wording", async () => {
    fixture.asset = () => ({ ...assetReady(), dependency_available: false });
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.dependencyMissing"));
    expect(body()).toContain(ko("localRetrieval.installRuntimeHelp"));
    expect(command()?.textContent).toBe(LOCAL_RETRIEVAL_INSTALL_COMMAND);
    expect(body()).toContain("설치한 뒤 THOTH를 다시 시작하세요.");
    expect(body().indexOf(ko("localRetrieval.dependencyMissing"))).toBeLessThan(
      body().indexOf(LOCAL_RETRIEVAL_INSTALL_COMMAND),
    );
    expect(server.fetch).not.toHaveBeenCalled();
  });

  it("with the runtime component shows no guide", async () => {
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.dependencyAvailable"));
    expect(command()).toBeNull();
    expect(body()).not.toContain(ko("localRetrieval.installRuntimeHelp"));
    expect(body()).not.toContain(ko("localRetrieval.installRuntimeRestart"));
  });

  it("in a hosted review workspace shows no guide", async () => {
    fixture.asset = () => ({ ...assetReady(), dependency_available: false });
    await render({ hosted: true, projectId: "p1" });
    expect(command()).toBeNull();
    expect(body()).not.toContain(ko("localRetrieval.installRuntimeHelp"));
    expect(body()).not.toContain(LOCAL_RETRIEVAL_INSTALL_COMMAND);
  });

  it("is shown in English with the same command", async () => {
    setLocale("en");
    fixture.asset = () => ({ ...assetReady(), dependency_available: false });
    await render({ projectId: "p1" });
    expect(body()).toContain(
      "To install it, run this command in PowerShell from the THOTH folder (optional, about 110 MB):",
    );
    expect(body()).toContain("After installing, restart THOTH.");
    expect(command()?.textContent).toBe(LOCAL_RETRIEVAL_INSTALL_COMMAND);
  });
});

describe("project index", () => {
  it("configure sends the expected generation and shows the server's answer", async () => {
    fixture.project = () => ({ ...projectOff(), generation: 3 });
    fixture.configure = (input) => ({
      operation_id: "configure",
      state: "SUCCEEDED",
      value: { ...projectEnabled(), generation: 4, enabled: Boolean(input.enabled) },
    });
    await render({ projectId: "p1" });
    await click(inputById("local-retrieval-enable")!);
    const sent = reads("project/retrieval/experiment/configure");
    expect(sent).toHaveLength(1);
    expect(sent[0].input).toEqual({ project_id: "p1", enabled: true, expected_generation: 3 });
    expect(inputById("local-retrieval-enable")!.checked).toBe(true);
    expect(body()).toContain(ko("localRetrieval.index.INDEX_PENDING"));
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(false);
  });

  it("a generation conflict keeps the old setting, explains it and reloads", async () => {
    fixture.project = () => ({ ...projectOff(), generation: 3 });
    fixture.configure = () => {
      throw Object.assign(new Error("conflict"), {
        name: "RpcError",
        code: -32040,
        details: { reason_code: "EMBEDDING_CONFIG_CONFLICT" },
      });
    };
    await render({ projectId: "p1" });
    const before = reads("project/retrieval/experiment/read").length;
    await click(inputById("local-retrieval-enable")!);
    await settle();
    expect(body()).toContain(ko("localRetrieval.configureConflict"));
    expect(inputById("local-retrieval-enable")!.checked).toBe(false);
    expect(reads("project/retrieval/experiment/read").length).toBeGreaterThan(before);
  });

  it("prepare and per-job retry send the project, generation and job identity", async () => {
    fixture.project = projectEnabled;
    await render({ projectId: "p1" });
    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "succeeded");
    expect(server.starts[0].method).toBe("project/retrieval/experiment/prepare");
    expect(server.starts[0].input).toEqual({ project_id: "p1", expected_generation: 1 });

    fixture.project = () => ({
      ...projectEnabled(),
      state: "FAILED",
      failed_sources: 1,
      pending_sources: 1,
      jobs: [
        { job_id: "job:a", generation: 2, state: "FAILED", reason_code: "EMBEDDING_TIMEOUT" },
        { job_id: "job:b", generation: 1, state: "PENDING", reason_code: null },
      ],
    });
    await refetch(["local-retrieval-project", "workspace:local", "p1"]);
    expect(body()).toContain(localRetrievalReason("EMBEDDING_TIMEOUT", "ko"));
    expect(body()).not.toContain("EMBEDDING_TIMEOUT");
    server.status.set("op:2", { state: "SUCCEEDED", result: { state: "PENDING" } });
    await click(button(ko("localRetrieval.retryButton")));
    await until(() => operationState() === "succeeded" && server.starts.length === 2);
    expect(server.starts[1].method).toBe("project/embeddingIndex/retry");
    expect(server.starts[1].input).toEqual({ project_id: "p1", job_id: "job:a", generation: 2 });
  });

  it("while the background indexer works on the project, switching off, prepare and retry stay usable", async () => {
    const working = (): RetrievalExperimentRead => ({
      ...projectEnabled(),
      state: "INDEXING",
      active_operation_id: null,
      failed_sources: 1,
      pending_sources: 1,
      jobs: [
        { job_id: "job:a", generation: 2, state: "FAILED", reason_code: "EMBEDDING_TIMEOUT" },
        { job_id: "job:b", generation: 1, state: "PENDING", reason_code: null },
      ],
    });
    fixture.project = working;
    fixture.configure = (input) => ({
      operation_id: "configure",
      state: "SUCCEEDED",
      value: { ...working(), generation: 2, enabled: Boolean(input.enabled), state: "OFF" },
    });
    await render({ projectId: "p1" });
    expect(inputById("local-retrieval-enable")!.disabled).toBe(false);
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(false);
    expect(isDisabled(button(ko("localRetrieval.retryButton")))).toBe(false);
    await click(inputById("local-retrieval-enable")!);
    expect(reads("project/retrieval/experiment/configure")).toHaveLength(1);
    expect(reads("project/retrieval/experiment/configure")[0].input).toEqual({
      project_id: "p1",
      enabled: false,
      expected_generation: 1,
    });
  });

  it("work a person started still holds the switch, prepare and retry", async () => {
    fixture.project = () => ({
      ...projectEnabled(),
      state: "INDEXING",
      active_operation_id: "op:started",
      failed_sources: 1,
      pending_sources: 1,
      jobs: [{ job_id: "job:a", generation: 2, state: "FAILED", reason_code: "EMBEDDING_TIMEOUT" }],
    });
    await render({ projectId: "p1" });
    expect(inputById("local-retrieval-enable")!.disabled).toBe(true);
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(true);
    expect(isDisabled(button(ko("localRetrieval.retryButton")))).toBe(true);
  });

  it("does not say the index is being rebuilt when nothing can run it", async () => {
    const outdated = (): RetrievalExperimentRead => ({ ...projectEnabled(), outdated_index: true });
    for (const [asset, project] of [
      [() => ({ ...assetReady(), dependency_available: false }), outdated],
      [assetMissing, outdated],
      [assetReady, () => ({ ...outdated(), failed_sources: 1, pending_sources: 1 })],
    ] as const) {
      fixture.asset = asset;
      fixture.project = project;
      await act(async () => {
        root.unmount();
        await tick();
      });
      root = createRoot(container);
      await render({ projectId: "p1" });
      expect(body()).not.toContain(ko("localRetrieval.outdatedIndex"));
    }
    fixture.asset = assetReady;
    fixture.project = outdated;
    await act(async () => {
      root.unmount();
      await tick();
    });
    root = createRoot(container);
    await render({ projectId: "p1" });
    expect(body()).toContain(ko("localRetrieval.outdatedIndex"));
  });

  it("a late result never reaches the screen of another project", async () => {
    fixture.project = projectEnabled;
    let release!: () => void;
    server.hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await render({ projectId: "p1" });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "running");
    const pollsBefore = server.polls.length;
    await render({ projectId: "p2" });
    expect(operationState()).toBeNull();
    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    server.hold = null;
    await act(async () => {
      release();
      await tick(60);
    });
    expect(operationState()).toBeNull();
    expect(body()).not.toContain(ko("localRetrieval.opSucceeded"));
    expect(server.polls.length).toBeLessThanOrEqual(pollsBefore + 1);
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(false);
  });

  it("a cancel request is shown apart from the server's confirmed stop", async () => {
    fixture.project = projectEnabled;
    let release!: () => void;
    server.hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await render({ projectId: "p1" });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => hasButton(ko("localRetrieval.cancelButton")));
    await click(button(ko("localRetrieval.cancelButton")));
    expect(server.cancels).toEqual([{ operationId: "op:1", projectId: "p1" }]);
    expect(body()).toContain(ko("localRetrieval.opCancelRequested"));
    expect(body()).not.toContain(ko("localRetrieval.opCancelled"));
    expect(operationState()).toBe("running");
    server.status.set("op:1", { state: "CANCELLED" });
    server.hold = null;
    await act(async () => {
      release();
      await tick();
    });
    await until(() => operationState() === "cancelled");
    expect(body()).toContain(ko("localRetrieval.opCancelled"));
    expect(hasButton(ko("localRetrieval.cancelButton"))).toBe(false);
  });

  it("a client time limit reads as unconfirmed; checking reuses the same work, running again starts new work", async () => {
    fixture.project = projectEnabled;
    await render({ projectId: "p1", timeouts: { ...FAST, totalMs: 60 } });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    expect(body()).toContain(ko("localRetrieval.opUnconfirmed"));
    expect(body()).not.toContain(ko("localRetrieval.opCancelled"));
    expect(server.cancels).toEqual([]);
    expect(server.starts).toHaveLength(1);
    const pollsBefore = server.polls.length;

    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    await click(button(ko("localRetrieval.confirmResultButton")));
    await until(() => operationState() === "succeeded");
    expect(server.starts).toHaveLength(1);
    expect(server.polls.length).toBeGreaterThan(pollsBefore);
    expect(server.polls.slice(pollsBefore)).toEqual(expect.arrayContaining(["op:1"]));
  });

  it("running again after an unconfirmed wait uses a new idempotency key and a new operation", async () => {
    fixture.project = projectEnabled;
    await render({ projectId: "p1", timeouts: { ...FAST, totalMs: 60 } });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    server.status.set("op:2", { state: "SUCCEEDED", result: { state: "READY" } });
    await click(button(ko("localRetrieval.newRunButton")));
    await until(() => operationState() === "succeeded");
    expect(server.starts).toHaveLength(2);
    expect(server.starts[1].idempotencyKey).not.toBe(server.starts[0].idempotencyKey);
    expect(server.starts[1].operationId).toBe("op:2");
  });

  it("an unconfirmed start keeps the same request for checking and distinguishes it from a new run", async () => {
    fixture.project = projectEnabled;
    let releaseStart!: () => void;
    server.startHold = new Promise<void>((resolve) => {
      releaseStart = resolve;
    });
    await render({ projectId: "p1", timeouts: { ...FAST, startMs: 30, totalMs: 60 } });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    expect(body()).toContain(ko("localRetrieval.opStartUnconfirmed"));
    expect(server.polls).toEqual([]);
    server.startHold = null;
    releaseStart();
    await settle();
    expect(server.starts).toHaveLength(1);
    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    await confirmUnknownStart();
  });
});

describe("time limits cover the response body, and unclear starts keep their key", () => {
  it("a start whose JSON body stalls is unconfirmed within the start limit, not after the whole wait", async () => {
    fixture.project = projectEnabled;
    let release!: () => void;
    server.startBodyHold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await render({ projectId: "p1", timeouts: { ...FAST, startMs: 30, totalMs: 5000 } });
    const started = Date.now();
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    expect(Date.now() - started).toBeLessThan(1500);
    expect(body()).toContain(ko("localRetrieval.opStartUnconfirmed"));
    expect(server.starts).toHaveLength(1);
    expect(server.polls).toEqual([]);
    server.startBodyHold = null;
    release();
    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    await confirmUnknownStart();
  });

  it("a result read whose JSON body stalls is unconfirmed within the read limit and keeps the operation", async () => {
    fixture.project = projectEnabled;
    let release!: () => void;
    server.pollBodyHold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await render({ projectId: "p1", timeouts: { ...FAST, pollMs: 30, totalMs: 5000 } });
    const started = Date.now();
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    expect(Date.now() - started).toBeLessThan(1500);
    expect(body()).toContain(ko("localRetrieval.opUnconfirmed"));
    expect(body()).not.toContain(ko("localRetrieval.opCancelled"));
    expect(server.polls).toEqual(["op:1"]);
    server.pollBodyHold = null;
    release();
    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    await click(button(ko("localRetrieval.confirmResultButton")));
    await until(() => operationState() === "succeeded");
    expect(server.starts).toHaveLength(1);
    expect(server.polls.length).toBeGreaterThan(1);
  });

  it("the start limit never exceeds what is left of the whole wait", async () => {
    fixture.project = projectEnabled;
    server.startHold = new Promise<void>(() => {});
    await render({ projectId: "p1", timeouts: { ...FAST, startMs: 5000, totalMs: 40 } });
    const started = Date.now();
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    expect(Date.now() - started).toBeLessThan(1500);
    expect(body()).toContain(ko("localRetrieval.opStartUnconfirmed"));
  });

  it.each([500, 503, 429, 408])(
    "start HTTP %s is unconfirmed, and checking reuses the same key",
    async (status) => {
      fixture.project = projectEnabled;
      server.startStatus = status;
      await render({ projectId: "p1" });
      await click(button(ko("localRetrieval.prepareButton")));
      await until(() => operationState() === "unconfirmed");
      expect(body()).toContain(ko("localRetrieval.opStartUnconfirmed"));
      expect(body()).not.toContain(ko("localRetrieval.opRejected"));
      expect(body()).not.toContain("SYNTHETIC");
      expect(server.polls).toEqual([]);
      server.startStatus = 200;
      server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
      await confirmUnknownStart();
    },
  );

  it.each([403, 409, 422])(
    "start HTTP %s with a JSON-RPC refusal is rejected without a check path",
    async (status) => {
      fixture.project = projectEnabled;
      server.startStatus = status;
      await render({ projectId: "p1" });
      await click(button(ko("localRetrieval.prepareButton")));
      await until(() => operationState() === "rejected");
      expect(body()).toContain(localRetrievalReason("LOCAL_RETRIEVAL_SCOPE_DENIED", "ko"));
      expect(hasButton(ko("localRetrieval.confirmResultButton"))).toBe(false);
      expect(hasButton(ko("localRetrieval.newRunButton"))).toBe(false);
    },
  );
});

describe("screen scope", () => {
  it("download consent, the typed folder and the last request stay with their workspace screen", async () => {
    fixture.asset = assetMissing;
    fixture.project = projectEnabled;
    await render({
      workspaceId: "workspace:a",
      projectId: "p1",
      timeouts: { ...FAST, totalMs: 60 },
    });
    await typeInto("local-retrieval-source-path", "/models/granite-copy");
    await click(inputById("local-retrieval-install-confirm")!);
    expect(inputById("local-retrieval-install-confirm")!.checked).toBe(true);
    expect(isDisabled(button(ko("localRetrieval.installButton")))).toBe(false);

    fixture.asset = assetReady;
    await refetch(["local-retrieval-asset", "workspace:a"]);
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "unconfirmed");
    expect(hasButton(ko("localRetrieval.confirmResultButton"))).toBe(true);

    fixture.asset = assetMissing;
    await render({
      workspaceId: "workspace:b",
      projectId: "p1",
      timeouts: { ...FAST, totalMs: 60 },
    });
    await settle();
    expect(inputById("local-retrieval-source-path")!.value).toBe("");
    expect(inputById("local-retrieval-install-confirm")!.checked).toBe(false);
    expect(isDisabled(button(ko("localRetrieval.installButton")))).toBe(true);
    expect(operationState()).toBeNull();
    expect(hasButton(ko("localRetrieval.confirmResultButton"))).toBe(false);
    const startsBefore = server.starts.length;

    await render({
      workspaceId: "workspace:a",
      projectId: "p1",
      timeouts: { ...FAST, totalMs: 60 },
    });
    await settle();
    expect(inputById("local-retrieval-source-path")!.value).toBe("");
    expect(inputById("local-retrieval-install-confirm")!.checked).toBe(false);
    expect(operationState()).toBeNull();
    expect(hasButton(ko("localRetrieval.confirmResultButton"))).toBe(false);
    expect(server.starts.length).toBe(startsBefore);
  });

  it("switching to a hosted screen and back shows nothing from the local screen's work", async () => {
    fixture.project = projectEnabled;
    let release!: () => void;
    server.hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await render({ projectId: "p1" });
    await click(button(ko("localRetrieval.prepareButton")));
    await until(() => operationState() === "running");
    const readsBefore = fixture.calls.length;
    await render({ projectId: "p1", hosted: true });
    expect(body()).toContain(ko("localRetrieval.hostedOnly"));
    expect(operationState()).toBeNull();
    server.status.set("op:1", { state: "SUCCEEDED", result: { state: "READY" } });
    server.hold = null;
    await act(async () => {
      release();
      await tick(60);
    });
    expect(fixture.calls.length).toBe(readsBefore);
    await render({ projectId: "p1" });
    await settle();
    expect(operationState()).toBeNull();
    expect(isDisabled(button(ko("localRetrieval.prepareButton")))).toBe(false);
  });
});

it("shows the parts every project shares on the computer's screen and the index on the project's, with no control on both", async () => {
  fixture.asset = assetMissing;
  await render({ part: "computer" });
  expect(container.querySelector("h2")!.textContent).toBe(ko("localRetrieval.title"));
  expect(inputById("local-retrieval-source-path")).not.toBeNull();
  expect(document.getElementById("local-retrieval-enable")).toBeNull();
  expect(container.querySelector("h3")).toBeNull();
  // Nothing about a project at all, not even "no project is selected".
  expect(body()).not.toContain(ko("localRetrieval.noProject"));
  expect(reads("project/retrieval/experiment/read")).toHaveLength(0);
  await act(async () => root.unmount());
  root = createRoot(container);
  await render({ part: "project", projectId: "p1" });
  expect(container.querySelector("h2")!.textContent).toBe(ko("localRetrieval.projectTitle"));
  expect(document.getElementById("local-retrieval-enable")).not.toBeNull();
  expect(inputById("local-retrieval-source-path")).toBeNull();
  expect(body()).not.toContain(ko("localRetrieval.importTitle"));
});
