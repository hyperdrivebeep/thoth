// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { setLocale, t } from "../i18n/locale";

type Progress = { phase: "uploading" | "processing"; loaded: number; total: number | null };
type Options = { signal?: AbortSignal; onProgress?: (progress: Progress) => void };
type Job = { name: string; options?: Options; finish: () => void; fail: () => void };
const fixture = vi.hoisted(() => ({ jobs: [] as Job[], calls: [] as string[], connectGate: null as Promise<void> | null, failConnect: "" }));
vi.mock("../api/files", () => ({ stageFile: (file: File, _project: string, options?: Options) => new Promise((resolve, reject) => {
  fixture.calls.push(`stage:${file.name}`);
  const cancel = () => reject(new Error("synthetic upload cancelled"));
  options?.signal?.addEventListener("abort", cancel, { once: true });
  fixture.jobs.push({ name: file.name, options, finish: () => {
    options?.signal?.removeEventListener("abort", cancel);
    resolve({ relative_path: `web/${file.name}`, filename: file.name, media_type: "text/markdown", byte_sha256: "a".repeat(64), bytes: file.size });
  }, fail: () => reject(new Error("synthetic upload stalled")) });
}) }));
vi.mock("../api/rpcClient", () => ({ rpc: async (_method: string, input: { relative_path: string }) => {
  fixture.calls.push(`connect:${input.relative_path}`);
  if (fixture.failConnect === input.relative_path) throw new Error("synthetic connect failure");
  if (fixture.connectGate) await fixture.connectGate;
  return { value: {}, state: "SUCCEEDED", operation_id: "op:upload" };
} }));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  fixture.jobs = []; fixture.calls = []; fixture.connectGate = null; fixture.failConnect = ""; setLocale("en");
  container = document.createElement("div"); document.body.append(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  root = createRoot(container);
  await act(async () => { root.render(<QueryClientProvider client={client}><FileConnectionPanel projectId="p" /></QueryClientProvider>); });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); setLocale("ko"); });
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function select(names: string[]) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: names.map(name => new File(["test"], name)) });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  const visibility = container.querySelector<HTMLSelectElement>(`[aria-label="${t("sources.scopeLabel")}"]`)!;
  await act(async () => { visibility.value = "PROJECT_SHARED"; visibility.dispatchEvent(new Event("change", { bubbles: true })); });
}
function button(label: string) { return [...container.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent === label); }
async function start(names: string[]) {
  await select(names);
  await act(async () => { button(t("composer.attach"))!.click(); });
  await tick();
}

it("stages the whole batch before connecting, preserving one-file order", async () => {
  await start(["A.md", "B.md"]);
  expect(fixture.calls).toEqual(["stage:A.md"]);
  await act(async () => fixture.jobs[0].finish()); await tick();
  expect(fixture.calls).toEqual(["stage:A.md", "stage:B.md"]);
  await act(async () => fixture.jobs[1].finish()); await tick();
  expect(fixture.calls).toEqual(["stage:A.md", "stage:B.md", "connect:web/A.md", "connect:web/B.md"]);
});

it.each(["cancel", "stall"])("connects zero sources when the second staged file ends by %s", async reason => {
  await start(["A.md", "B.md", "C.md"]);
  await act(async () => fixture.jobs[0].finish()); await tick();
  expect(fixture.jobs.map(job => job.name)).toEqual(["A.md", "B.md"]);
  if (reason === "cancel") {
    expect(button("Cancel upload")).toBeDefined();
    await act(async () => button("Cancel upload")!.click());
  } else await act(async () => fixture.jobs[1].fail());
  await tick();
  expect(fixture.calls.filter(call => call.startsWith("connect:"))).toEqual([]);
  expect(fixture.jobs.map(job => job.name)).toEqual(["A.md", "B.md"]);
  expect(button("Cancel upload")).toBeUndefined();
});

it("shows progress and waiting, and hides cancel before connect begins", async () => {
  let release!: () => void;
  fixture.connectGate = new Promise<void>(resolve => { release = resolve; });
  await start(["A.md"]);
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "uploading", loaded: 50, total: 100 }));
  expect(container.textContent).toContain("Uploading A.md (50%)");
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "processing", loaded: 100, total: 100 }));
  expect(container.textContent).toContain("A.md was sent. The server is processing it…");
  await act(async () => fixture.jobs[0].finish()); await tick();
  expect(fixture.calls).toEqual(["stage:A.md", "connect:web/A.md"]);
  expect(button("Cancel upload")).toBeUndefined();
  await act(async () => release()); await tick();
});

it("preserves a newer selection when an earlier batch is cancelled", async () => {
  await start(["A.md"]);
  await select(["B.md"]);
  expect(button("Cancel upload")).toBeDefined();
  await act(async () => button("Cancel upload")!.click()); await tick();
  expect(container.textContent).toContain("B.md");
  expect(fixture.calls).toEqual(["stage:A.md"]);
});

it("keeps the existing partial-connect error guidance and cannot cancel a connect", async () => {
  fixture.failConnect = "web/B.md";
  await start(["A.md", "B.md", "C.md"]);
  for (let index = 0; index < 3; index++) {
    await act(async () => fixture.jobs[index].finish()); await tick();
  }
  expect(fixture.calls).toEqual(["stage:A.md", "stage:B.md", "stage:C.md", "connect:web/A.md", "connect:web/B.md"]);
  expect(button("Cancel upload")).toBeUndefined();
  expect(container.textContent).toContain("synthetic connect failure");
  expect(container.textContent).toContain(t("sources.checkConnected"));
});

it("aborts staging on unmount and never connects a late result", async () => {
  await start(["A.md"]);
  const job = fixture.jobs[0];
  await act(async () => root.unmount()); await tick();
  expect(job.options?.signal?.aborted).toBe(true);
  await act(async () => job.finish()); await tick();
  expect(fixture.calls).toEqual(["stage:A.md"]);
});

it("keeps cancellation available during processing and connects zero sources", async () => {
  await start(["report.pdf"]);
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "processing", loaded: 100, total: 100 }));
  expect(button("Cancel upload")).toBeDefined();
  await act(async () => setLocale("ko"));
  expect(container.textContent).toContain("report.pdf 파일을 다 보냈습니다. 서버가 처리하는 중입니다…");
  await act(async () => button("업로드 취소")!.click()); await tick();
  expect(fixture.calls).toEqual(["stage:report.pdf"]);
});
