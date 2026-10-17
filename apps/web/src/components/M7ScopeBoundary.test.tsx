// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readDraft } from "../api/conversation";
import { useCitationGate } from "./SourceCitationGate";
import { useResearchSession } from "./useResearchSession";

const fixture = vi.hoisted(() => ({ gate: null as Promise<void> | null, calls: [] as string[] }));
vi.mock("../api/rpcClient", () => ({ rpc: async (method: string) => {
  fixture.calls.push(method);
  if (method === "project/source/list") {
    if (fixture.gate) await fixture.gate;
    return { value: { artifacts: [{ artifact_id: "artifact:fake", cutoff_state: "ELIGIBLE" }] } };
  }
  if (method === "thread/read") return { value: { operation_state: "SUCCEEDED" } };
  if (method === "thread/input") return { state: "RUNNING", operation_id: "operation:fake", value: {
    contract_version: 2, project_id: "p", thread_id: "t", request_epoch: 2,
    operation_id: "operation:fake", status: "ACCEPTED_RUNNING",
  } };
  throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
} }));

let root: Root, container: HTMLDivElement, client: QueryClient;
let citation: ReturnType<typeof useCitationGate>;
let session: ReturnType<typeof useResearchSession>;
let allowed = true;
const epoch = { current: 0 };
const scope = { mode: "LOCAL" as const, workspaceId: "workspace:m7-guard" };
const tick = () => new Promise(resolve => setTimeout(resolve, 15));
function CitationSurface() { citation = useCitationGate("p", () => allowed); return <>{citation.message}{citation.dialog}</>; }
function SessionSurface() { session = useResearchSession("p", "t", epoch, () => {}, scope, true); return <>{session.problem}</>; }
async function render(node: ReactNode) {
  await act(async () => { root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>); await tick(); });
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  allowed = true; epoch.current = 0; fixture.gate = null; fixture.calls = [];
  localStorage.clear(); sessionStorage.clear();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("REAL_FETCH_FORBIDDEN"); }));
});
afterEach(async () => {
  await act(async () => { root.unmount(); await tick(); });
  client.clear(); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  localStorage.clear(); sessionStorage.clear();
});

it("does not write cache or messages if ownership changes during query cancellation", async () => {
  await render(<CitationSurface />);
  let release!: () => void;
  const pendingCancel = new Promise<void>(resolve => { release = resolve; });
  const cancel = vi.spyOn(client, "cancelQueries").mockReturnValue(pendingCancel);
  const pending = citation.ask("artifact:fake");
  await act(async () => { await tick(); });
  expect(cancel).toHaveBeenCalledTimes(1);
  allowed = false;
  const nextScope = { value: { artifacts: [{ artifact_id: "artifact:new-scope" }] } };
  client.setQueryData(["sources", "p"], nextScope);
  await act(async () => { release(); expect(await pending).toBeNull(); });
  expect(client.getQueryData(["sources", "p"])).toEqual(nextScope);
  expect(citation.message).toBe("");
  expect(citation.dialog).toBeNull();
});

it("quietly discards a rejected citation read after ownership changes", async () => {
  await render(<CitationSurface />);
  let reject!: (error: Error) => void;
  fixture.gate = new Promise<void>((_resolve, failure) => { reject = failure; });
  const pending = citation.ask("artifact:fake");
  expect(fixture.calls).toEqual(["project/source/list"]);
  allowed = false;
  await act(async () => { reject(new Error("SYNTHETIC_LATE_NETWORK_FAILURE")); expect(await pending).toBeNull(); });
  expect(client.getQueryData(["sources", "p"])).toBeUndefined();
  expect(citation.message).toBe("");
});

it("a retained send cannot borrow a newer epoch, while a current send still works", async () => {
  await render(<SessionSurface />);
  await act(async () => { session.setProblem("Synthetic draft remains owned"); });
  const oldSend = session.send;
  epoch.current += 1;
  await act(async () => { oldSend(); await tick(); });
  expect(fixture.calls.filter(method => method === "thread/input")).toHaveLength(0);
  expect(readDraft("p", "t", scope)).toBe("Synthetic draft remains owned");
  await render(<SessionSurface />);
  await act(async () => { session.send(); await tick(); });
  expect(fixture.calls.filter(method => method === "thread/input")).toHaveLength(1);
});
