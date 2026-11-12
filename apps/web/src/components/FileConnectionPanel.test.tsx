// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { setLocale, t } from "../i18n/locale";
import { UPLOAD_ACCEPT, UploadRefused } from "../api/uploadTypes";

type Progress = { phase: "uploading" | "processing"; loaded: number; total: number | null };
type Options = { signal?: AbortSignal; onProgress?: (progress: Progress) => void };
type Job = { name: string; options?: Options; finish: () => void; fail: (error?: Error) => void };
const fixture = vi.hoisted(() => ({ jobs: [] as Job[], calls: [] as string[], scopes: [] as string[], connectGate: null as Promise<void> | null, failConnect: "" }));
vi.mock("../api/files", () => ({ stageFile: (file: File, _project: string, options?: Options) => new Promise((resolve, reject) => {
  fixture.calls.push(`stage:${file.name}`);
  const cancel = () => reject(new Error("synthetic upload cancelled"));
  options?.signal?.addEventListener("abort", cancel, { once: true });
  fixture.jobs.push({ name: file.name, options, finish: () => {
    options?.signal?.removeEventListener("abort", cancel);
    resolve({ relative_path: `web/${file.name}`, filename: file.name, media_type: "text/markdown", byte_sha256: "a".repeat(64), bytes: file.size });
  }, fail: (error?: Error) => reject(error ?? new Error("synthetic upload stalled")) });
}) }));
vi.mock("../api/rpcClient", () => ({ rpc: async (_method: string, input: { relative_path: string; resource_scope: { visibility: string } }) => {
  fixture.calls.push(`connect:${input.relative_path}`);
  fixture.scopes.push(input.resource_scope.visibility);
  if (fixture.failConnect === input.relative_path) throw new Error("synthetic connect failure");
  if (fixture.connectGate) await fixture.connectGate;
  return { value: {}, state: "SUCCEEDED", operation_id: "op:upload" };
} }));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  fixture.jobs = []; fixture.calls = []; fixture.scopes = []; fixture.connectGate = null; fixture.failConnect = ""; setLocale("en");
  container = document.createElement("div"); document.body.append(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  root = createRoot(container);
  await act(async () => { root.render(<QueryClientProvider client={client}><FileConnectionPanel projectId="p" /></QueryClientProvider>); });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); setLocale("ko"); });
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function selectFiles(files: File[]) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: files });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
}
// On a local workspace nothing else has to be chosen: the usage scope starts as shared within the project.
const select = (names: string[]) => selectFiles(names.map(name => new File(["test"], name)));
const scopeSelect = () => container.querySelector<HTMLSelectElement>(`[aria-label="${t("sources.scopeLabel")}"]`);
const choose = (node: HTMLSelectElement, value: string) => act(async () => { node.value = value; node.dispatchEvent(new Event("change", { bubbles: true })); });
const alertText = () => container.querySelector('[role="alert"]')?.textContent ?? "";
function button(label: string) { return [...container.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent === label); }
async function start(names: string[]) {
  await select(names);
  await act(async () => { button(t("composer.attach"))!.click(); });
  await tick();
}

function browseText() { return container.querySelector(".bp6-file-upload-input")?.getAttribute("bp6-button-text"); }

it("shows the file-picker button text in the screen language instead of Blueprint's English default", async () => {
  expect(browseText()).toBe("Browse");
  await act(async () => setLocale("ko"));
  expect(browseText()).toBe("찾아보기");
  await act(async () => setLocale("ja"));
  expect(browseText()).toBe("参照");
  await act(async () => setLocale("zh-Hans"));
  expect(browseText()).toBe("浏览");
});

it("reads the project list again after sources are connected, because the project revision went up", async () => {
  const spy = vi.spyOn(client, "invalidateQueries");
  await start(["A.md"]);
  await act(async () => fixture.jobs[0].finish()); await tick();
  expect(spy.mock.calls.some(([filter]) => JSON.stringify(filter?.queryKey) === JSON.stringify(["projects"]))).toBe(true);
});

it("also reads the project list again when a later file of the batch fails to connect", async () => {
  const spy = vi.spyOn(client, "invalidateQueries");
  fixture.failConnect = "web/B.md";
  await start(["A.md", "B.md"]);
  await act(async () => fixture.jobs[0].finish()); await tick();
  await act(async () => fixture.jobs[1].finish()); await tick();
  expect(fixture.calls.filter(call => call.startsWith("connect:"))).toEqual(["connect:web/A.md", "connect:web/B.md"]);
  expect(spy.mock.calls.some(([filter]) => JSON.stringify(filter?.queryKey) === JSON.stringify(["projects"]))).toBe(true);
});

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
  // The title says one thing, and the sentence says what became of the rest of the selection.
  expect(alertText()).toContain("The source was not connected");
  expect(alertText()).not.toContain("may be connected");
  expect(alertText()).toContain("1 of the 3 selected files was connected before this one. Check the source list.");
});

it("connects a chosen file on a local workspace with no other choice, shared within the project", async () => {
  expect(button(t("composer.attach"))!.disabled).toBe(true);
  // The scope is said in one line; its control is under the fold.
  expect(container.textContent).toContain("Usage scope: Shared within this project");
  expect(scopeSelect()).toBeNull();
  await select(["A.md"]);
  expect(button(t("composer.attach"))!.disabled).toBe(false);
  await act(async () => { button(t("composer.attach"))!.click(); }); await tick();
  await act(async () => fixture.jobs[0].finish()); await tick();
  expect(fixture.scopes).toEqual(["PROJECT_SHARED"]);
});

it("lets the scope be changed under the fold, and sends the changed one", async () => {
  await act(async () => { button("Usage scope: Shared within this project")!.click(); });
  expect([...scopeSelect()!.options].map(option => option.value)).toEqual(["PROJECT_SHARED", "EXPLICIT_GRANT"]);
  expect(container.textContent).toContain("lets every task in this project use the source as evidence");
  await choose(scopeSelect()!, "EXPLICIT_GRANT");
  expect(button("Usage scope: Explicitly permitted tasks only")).toBeDefined();
  await start(["A.md"]);
  await act(async () => fixture.jobs[0].finish()); await tick();
  expect(fixture.scopes).toEqual(["EXPLICIT_GRANT"]);
});

it("still asks for the scope in the hosted review environment", async () => {
  await act(async () => { root.render(<QueryClientProvider client={client}><FileConnectionPanel key="hosted" projectId="p" hosted /></QueryClientProvider>); });
  await select(["A.md"]);
  expect(scopeSelect()!.value).toBe("");
  expect(button(t("composer.attach"))!.disabled).toBe(true);
  await choose(scopeSelect()!, "PROJECT_SHARED");
  expect(button(t("composer.attach"))!.disabled).toBe(false);
});

it("offers only the types the server stages in the file dialog", () => {
  expect(container.querySelector<HTMLInputElement>('input[type="file"]')!.accept).toBe(UPLOAD_ACCEPT);
  expect(UPLOAD_ACCEPT.split(",")).toContain(".hwpx");
});

it("refuses a file of another type before sending it, naming the file and what can be uploaded", async () => {
  await act(async () => setLocale("ja"));
  await start(["setup.exe"]);
  expect(fixture.calls).toEqual([]);
  expect(alertText()).toContain("資料を接続できませんでした");
  expect(alertText()).toContain("「setup.exe」はアップロードできない形式です。アップロードできる形式：HWPX · PDF · DOCX · XLSX · CSV · JSON · MD · TXT · YAML · HTML。");
  // No sentence written by the server in English, and nothing about other files: there was only this one.
  expect(alertText()).not.toMatch(/unsupported research file type/);
  expect(alertText()).not.toContain("選択した");
  await act(async () => setLocale("ko"));
  expect(alertText()).toContain('"setup.exe" 파일은 올릴 수 없는 형식입니다. 올릴 수 있는 형식: HWPX');
});

it("refuses an empty file before sending it, and says that none of the selection was connected", async () => {
  await selectFiles([new File(["test"], "A.md"), new File([], "empty.md")]);
  await act(async () => { button(t("composer.attach"))!.click(); }); await tick();
  expect(fixture.calls).toEqual([]);
  expect(alertText()).toContain('"empty.md" is empty. Upload a file that has content.');
  expect(alertText()).toContain("None of the 2 selected files was connected.");
});

it("says a refusal the server names by code in the screen language", async () => {
  await start(["big.pdf"]);
  await act(async () => fixture.jobs[0].fail(new UploadRefused("UPLOAD_TOO_LARGE", "big.pdf"))); await tick();
  expect(alertText()).toContain('"big.pdf" is larger than 64 MB. Split it into smaller files.');
  await act(async () => setLocale("zh-Hans"));
  expect(alertText()).toContain("“big.pdf”超过 64MB。请拆分成较小的文件后上传。");
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

it("shows the source connection panel and the upload progress in Simplified Chinese", async () => {
  await act(async () => setLocale("zh-Hans"));
  expect(container.textContent).toContain("连接资料文件");
  await start(["report.pdf"]);
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "uploading", loaded: 50, total: 100 }));
  expect(container.textContent).toContain("正在上传 report.pdf（50%）");
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "processing", loaded: 100, total: 100 }));
  expect(container.textContent).toContain("report.pdf 已发送完毕。服务器正在处理…");
  expect(container.textContent).not.toMatch(/[가-힣]/);
  expect(button("取消上传")).toBeDefined();
  await act(async () => button("取消上传")!.click()); await tick();
  expect(fixture.calls).toEqual(["stage:report.pdf"]);
});

it("shows the source connection panel and the upload progress in Japanese", async () => {
  await act(async () => setLocale("ja"));
  expect(container.textContent).toContain("資料ファイルを接続");
  await start(["report.pdf"]);
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "uploading", loaded: 50, total: 100 }));
  expect(container.textContent).toContain("report.pdf を送信中（50%）");
  await act(async () => fixture.jobs[0].options?.onProgress?.({ phase: "processing", loaded: 100, total: 100 }));
  expect(container.textContent).toContain("report.pdf を送信し終えました。サーバーが処理しています…");
  expect(container.textContent).not.toMatch(/[가-힣]/);
  expect(button("アップロードをキャンセル")).toBeDefined();
  await act(async () => button("アップロードをキャンセル")!.click()); await tick();
  expect(fixture.calls).toEqual(["stage:report.pdf"]);
});
