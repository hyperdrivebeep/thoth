// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { rpc } from "../api/rpcClient";
import { cutoffLabel } from "../api/sourceTime";
import { SourceTimeAdvanced } from "./SourceTimeAdvanced";
import { useCitationGate } from "./SourceCitationGate";

const server = vi.hoisted(() => ({ state: "UNKNOWN_TIME", revision: 1, calls: [] as string[] }));
function assessment() {
  return { artifact_id: "a", source_version_id: "v", byte_sha256: "b".repeat(64), cutoff_state: server.state,
    revision: server.revision, assessment_digest: "a".repeat(64), metadata_digest: "c".repeat(64),
    reason_code: "TEST", cutoff_at: "2026-09-21T00:00:00Z", mode: "AUTO" };
}
function sourceList() {
  return { artifacts: [{ artifact_id: "a", source_uri: "fixture.md", byte_sha256: "b".repeat(64), cutoff_state: server.state }],
    source_times: [assessment()], cutoff_basis: { cutoff_at: "2026-09-21T00:00:00Z", project_revision: 7 } };
}
vi.mock("../api/rpcClient", () => ({
  rpc: async (method: string, input: Record<string, unknown>) => {
    server.calls.push(method);
    let value: unknown;
    if (method === "project/source/list") value = sourceList();
    else if (method === "evidence/list") value = { cutoff_state: server.state };
    else if (method === "project/source/time/confirm" || method === "project/source/time/correct") {
      server.state = input.revert_unknown ? "UNKNOWN_TIME" : input.assertion === "AFTER_CUTOFF" ? "AFTER_CUTOFF" : "ELIGIBLE";
      server.revision += 1;
      value = { source_time: assessment() };
    } else throw new Error(`Unexpected test method: ${method}`);
    return { operation_id: "test", state: "SUCCEEDED", value };
  },
}));

function Surface({ advanced }: { advanced: boolean }) {
  const source = useQuery({ queryKey: ["sources", "p"], queryFn: () => rpc<ReturnType<typeof sourceList>>("project/source/list", { project_id: "p" }, "read") });
  const evidence = useQuery({ queryKey: ["evidence", "p"], queryFn: () => rpc<{cutoff_state:string}>("evidence/list", { project_id: "p" }, "evidence") });
  const citation = useCitationGate("p");
  return <>
    <p data-testid="source-state">{cutoffLabel(source.data?.value.source_times[0].cutoff_state ?? "UNKNOWN_TIME")}</p>
    <p data-testid="evidence-state">{evidence.data?.value.cutoff_state}</p>
    <button onClick={() => void citation.ask("a")}>인용에 사용</button>{citation.dialog}
    {advanced && source.data && <SourceTimeAdvanced projectId="p" artifactId="a" assessment={source.data.value.source_times[0]} cutoff={source.data.value.cutoff_basis} />}
  </>;
}
let root: Root | undefined, container: HTMLDivElement, client: QueryClient;
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); };
async function mount(advanced = false) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(["sources", "other-project"], { untouched: true });
  client.setQueryData(["research", "p", "thread"], { state: "PREVIOUS_VIEW" });
  await act(async () => root!.render(<QueryClientProvider client={client}><Surface advanced={advanced} /></QueryClientProvider>));
  await flush();
}
async function click(text: string) {
  const button = [...document.querySelectorAll("button")].find(node => node.textContent === text);
  expect(button).toBeTruthy(); await act(async () => button!.click()); await flush();
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount()); root = undefined;
  client?.clear(); container?.remove(); server.state = "UNKNOWN_TIME"; server.revision = 1; server.calls = [];
});

it.each([['기준시점까지 존재', 'ELIGIBLE'], ['기준시점보다 후', 'AFTER_CUTOFF']])("refreshes canonical labels after citation confirmation: %s", async (button, expected) => {
  await mount(); await click("인용에 사용"); await click(button);
  expect(container.querySelector('[data-testid="source-state"]')?.textContent).toBe(cutoffLabel(expected));
  expect(container.querySelector('[data-testid="evidence-state"]')?.textContent).toBe(expected);
  expect(client.getQueryState(["research", "p", "thread"])?.isInvalidated).toBe(true);
  expect(client.getQueryState(["sources", "other-project"])?.isInvalidated).toBe(false);
  expect(server.calls.filter(method => method === "project/source/time/confirm")).toHaveLength(1);
});

it.each([['기준시점까지 존재로 정정', 'ELIGIBLE'], ['기준시점보다 후로 정정', 'AFTER_CUTOFF'], ['미확인으로 되돌림', 'UNKNOWN_TIME']])("refreshes labels after advanced correction: %s", async (button, expected) => {
  server.state = expected === "UNKNOWN_TIME" ? "ELIGIBLE" : "UNKNOWN_TIME";
  await mount(true);
  await click("고급 · 자료의 기준시점");
  await flush(); await click(button);
  expect(container.querySelector('[data-testid="source-state"]')?.textContent).toBe(cutoffLabel(expected));
  expect(container.querySelector('[data-testid="evidence-state"]')?.textContent).toBe(expected);
  expect(container.querySelector<HTMLSelectElement>('select[aria-label="기준시점 상태"]')?.value).toBe(expected);
  expect(client.getQueryState(["sources", "other-project"])?.isInvalidated).toBe(false);
});

it("updates the displayed source list when the citation read observes an already confirmed source", async () => {
  await mount(); server.state = "ELIGIBLE"; server.revision = 2;
  await click("인용에 사용");
  expect(container.querySelector('[data-testid="source-state"]')?.textContent).toBe(cutoffLabel("ELIGIBLE"));
  expect(server.calls).not.toContain("project/source/time/confirm");
});
