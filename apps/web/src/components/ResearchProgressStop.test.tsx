// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ResearchStatus } from "../api/research";
import { ResearchProgress } from "./ResearchProgress";

const calls = vi.hoisted(() => [] as string[]);
vi.mock("../api/rpcClient", async importOriginal => ({
  ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string) => {
    calls.push(method);
    return { value: {}, state: "SUCCEEDED", operation_id: "op" };
  },
}));

let root: Root;
let container: HTMLDivElement;
const running = { project_id: "p", thread_id: "t", problem: "q", operation_state: "RUNNING", execution_state: "RUNNING" } as unknown as ResearchStatus;
const button = (scope: ParentNode, label: string) => [...scope.querySelectorAll("button")].find(item => item.textContent === label) as HTMLButtonElement;
const dialog = () => document.querySelector(".confirm-dialog");

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchProgress status={running} /></QueryClientProvider>));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  calls.length = 0;
  vi.restoreAllMocks();
});

it("asks before stopping, in its own window, and sends nothing when the person cancels", async () => {
  const browserConfirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  await act(async () => button(container, "중단 요청").click());
  expect(dialog()?.textContent).toContain("조사 중단");
  expect(browserConfirm).not.toHaveBeenCalled();
  expect(calls).toEqual([]);
  await act(async () => button(dialog()!, "취소").click());
  expect(dialog()).toBeNull();
  expect(calls).toEqual([]);
});

it("stops once the person confirms", async () => {
  await act(async () => button(container, "중단 요청").click());
  await act(async () => button(dialog()!, "중단 요청").click());
  expect(dialog()).toBeNull();
  expect(calls).toEqual(["thread/stop"]);
});
