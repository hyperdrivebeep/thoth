// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectReviewList } from "./ProjectReviewList";

const item = { item_id: "t:1", project_id: "project:p", thread_id: "thread:t", request_revision_digest: "a".repeat(64), result_revision_digest: null, title: "추가 질문에 대한 답변", priority: "HIGH",
  reason_codes: ["SOURCE_BASIS_CHANGED", "SOMETHING_NEW_HAPPENED", "MEMORY_CORRECTED_AFTER_RESULT"], currentness: { state: "REVIEW_REQUIRED", reasons: [] },
  next_user_action: { schema_version: "1.0.0", action_type: "REVIEW_CURRENTNESS", label: "현재 자료 기준으로 다시 확인", reason_codes: [], requires_permission: false, target: null, basis: {} } };
vi.mock("../../api/researchFollowup", async importOriginal => ({ ...(await importOriginal<typeof import("../../api/researchFollowup")>()),
  readProjectReviewList: async () => ({ items: [item], next_cursor: null, unread_supported: true, assignment_supported: true }) }));

let root: Root | undefined; let container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); });

it("shows the reasons as sentences and keeps the raw codes under technical details", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root!.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProjectReviewList projectId="project:p" onSelect={() => undefined}/></QueryClientProvider>));
  for (let i = 0; i < 10; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
  const copy = container.cloneNode(true) as HTMLElement;
  copy.querySelectorAll("details").forEach(node => node.remove());
  expect(copy.textContent).toContain("답변이 쓴 자료가 이후 바뀌었습니다.");
  expect(copy.textContent).toContain("기록된 다른 이유가 있습니다.");
  expect(copy.textContent).not.toMatch(/SOURCE_BASIS_CHANGED|SOMETHING_NEW_HAPPENED|MEMORY_CORRECTED_AFTER_RESULT/);
  expect(container.querySelector("details")?.textContent).toContain("SOURCE_BASIS_CHANGED");
});
