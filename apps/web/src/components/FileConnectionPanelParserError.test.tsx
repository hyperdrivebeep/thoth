// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { setLocale, t } from "../i18n/locale";

const fixture = vi.hoisted(() => ({ reason: "" as string | null }));
vi.mock("../api/files", () => ({ stageFile: async (file: File) => ({ relative_path: `web/${file.name}`, filename: file.name, media_type: "text/plain", byte_sha256: "a".repeat(64), bytes: file.size }) }));
vi.mock("../api/rpcClient", async importOriginal => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  return { ...actual, rpc: async () => { throw new actual.RpcError("source parsing failed", -32030, fixture.reason === null ? { connector_error: "PARSER_FAILURE" } : { connector_error: "PARSER_FAILURE", parser_error: fixture.reason }); } };
});

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  root = createRoot(container);
  await act(async () => { root.render(<QueryClientProvider client={client}><FileConnectionPanel projectId="p" /></QueryClientProvider>); });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); setLocale("ko"); });

async function connectFile() {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["x"], "report.txt")] });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  await act(async () => { [...container.querySelectorAll("button")].find(item => item.textContent === t("composer.attach"))!.click(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}
const callout = () => container.querySelector(".bp6-callout")!;

it("says in words what to do when the text encoding is not confirmed, and folds the code away", async () => {
  fixture.reason = "TEXT_ENCODING_UNCONFIRMED";
  await connectFile();
  const details = callout().querySelector("details")!;
  expect(callout().textContent).toContain("글자 인코딩을 확인하지 못했습니다. 파일을 UTF-8로 저장해 다시 올려 주세요.");
  expect(details.open).toBe(false);
  expect(details.querySelector("summary")!.textContent).toBe("기술 정보");
  expect(details.textContent).toContain("source parsing failed [-32030]");
  expect(details.textContent).toContain("TEXT_ENCODING_UNCONFIRMED");
  expect(callout().textContent).toContain("자료를 연결하지 못했습니다");
  // One file was chosen, so there is no sentence about the rest of a selection.
  expect(callout().textContent).not.toContain("선택한 파일");
});

it("uses the English sentence when the screen is in English", async () => {
  await act(async () => setLocale("en"));
  fixture.reason = "OCR_REQUIRED";
  await connectFile();
  expect(callout().textContent).toContain("The text is only an image");
  expect(callout().querySelector("summary")!.textContent).toBe("Technical details");
});

it("shows the general sentence and the code for a reason the screen has no sentence for", async () => {
  fixture.reason = "A_CODE_FROM_A_NEWER_SERVER";
  await connectFile();
  expect(callout().textContent).toContain(t("parserError.unknown"));
  expect(callout().querySelector("details")!.textContent).toContain("A_CODE_FROM_A_NEWER_SERVER");
});

it("keeps the plain message when the error is not a parser reason", async () => {
  fixture.reason = null;
  await connectFile();
  expect(callout().textContent).toContain("source parsing failed [-32030]");
  expect(callout().querySelector("details")).toBeNull();
});
