// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { HypothesisLink } from "../api/hypothesisLink";
import { HypothesisLinkNotice } from "./HypothesisLinkNotice";

const calls = vi.hoisted(() => [] as { method: string; input: Record<string, unknown> }[]);
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    calls.push({ method, input });
    return { value: { events: [], links: [], reason_distribution: { total: 0, flipped: 0, by_reason: {} }, trace_digest: null }, state: "SUCCEEDED", operation_id: "op" };
  } }));

const BANNED = /재시험 필수|원인은|기준을 낮추|통과 가능|승인됨|종결|해결|면제|AI 추천/;
const link = (patch: Partial<HypothesisLink> = {}): HypothesisLink => ({
  hypothesis_id: "h1", hypothesis_revision_digest: "d".repeat(64), statement: "RFP 문구를 옮겨 읽었을 수 있다", subject_kind: "CRITERION", subject_id: "C-1",
  subject_title: "비 조건 탐지율", state: "STALE", change: "CONTENT_CHANGED", link_state: "HOLD_NO_RESULT", current_state: "FAIL_COMPUTED",
  current_verdict_revision: "r".repeat(64), recheck: null, ...patch,
});
let root: Root | undefined; let container: HTMLDivElement; let client: QueryClient;
beforeEach(() => { calls.length = 0; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; });
async function mount(item: HypothesisLink | undefined, all: HypothesisLink[] = item ? [item] : []) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><HypothesisLinkNotice projectId="p" link={item} all={all}/></QueryClientProvider>));
}
const dialog = () => document.body.querySelector<HTMLElement>('[role="dialog"]');
const button = (label: string, scope: ParentNode = document.body) => [...scope.querySelectorAll("button")].find(item => item.textContent?.trim() === label) as HTMLButtonElement | undefined;
const click = async (element: Element | undefined | null) => { expect(element).toBeTruthy(); await act(async () => (element as HTMLElement).click()); };
const choose = async (label: string) => click([...dialog()!.querySelectorAll("label")].find(item => item.textContent?.includes(label))!.querySelector("input"));
const write = async (text: string) => {
  const area = dialog()!.querySelector("textarea")!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(area, text); area.dispatchEvent(new Event("input", { bubbles: true })); });
};
const visible = () => container.textContent! + (dialog()?.textContent ?? "");

it("shows nothing for a current hypothesis or one without a link", async () => {
  await mount(link({ state: "CURRENT", change: null }));
  expect(container.textContent).toBe("");
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  await mount(undefined);
  expect(container.textContent).toBe("");
});

it("marks a hypothesis on a changed verdict as the old basis, with what it was and what it is, and says it is not used for approval", async () => {
  await mount(link());
  const text = container.textContent!;
  expect(text).toContain("이전 근거 기준 — 판정이 바뀌었습니다(이전: 보류 · 결과 없음 → 지금: 기준 미달)");
  expect(text).toContain("「비 조건 탐지율」 기준");
  expect(text).toContain("행동 승인과 실행에 쓰이지 않습니다");
  expect(button("다시 확인")).toBeTruthy();
  expect(text).not.toMatch(BANNED); expect(text).not.toMatch(/CONTENT_CHANGED|HOLD_|FAIL_|STALE|verdict/); expect(text).not.toMatch(/\d\s*%|확률/);
});

it("needs a reason, needs words for 'other', and sends the reason, the words and the verdict the person saw", async () => {
  await mount(link());
  await click(button("다시 확인"));
  expect(dialog()).toBeTruthy();
  await click(button("확인 기록 남기기", dialog()!));
  expect(dialog()!.textContent).toContain("이유를 하나 고르세요.");
  await choose("기타");
  await click(button("확인 기록 남기기", dialog()!));
  expect(dialog()!.textContent).toContain("이 이유는 글로 적어야 합니다.");
  expect(calls).toHaveLength(0);
  await write("다른 장비로 잰 결과입니다");
  await click(button("확인 기록 남기기", dialog()!));
  expect(calls).toEqual([{ method: "hypothesis/link/recheck", input: { project_id: "p", hypothesis_ids: ["h1"], reason_code: "OTHER", note: "다른 장비로 잰 결과입니다", current_verdict_revision: "r".repeat(64) } }]);
});

it("does not ask for words for an ordinary change, but does for keeping a link across met and failed, and never for a research request", async () => {
  await mount(link());
  await click(button("다시 확인"));
  await choose("판정이 바뀌었지만 이 가설과 관계없음");
  await click(button("확인 기록 남기기", dialog()!));
  expect(calls[0].input).toMatchObject({ reason_code: "UNRELATED", note: "" });
  await act(async () => root!.unmount()); container.remove(); root = undefined; document.body.innerHTML = ""; calls.length = 0;
  await mount(link({ change: "FLIPPED", link_state: "PASS_COMPUTED" }));
  await click(button("다시 확인"));
  expect(visible()).toContain("충족과 미달이 뒤바뀌었습니다");
  await choose("새 결과도 이 가설과 맞음");
  await click(button("확인 기록 남기기", dialog()!));
  expect(dialog()!.textContent).toContain("이 이유는 글로 적어야 합니다.");
  expect(calls).toHaveLength(0);
  await choose("다시 조사가 필요함");
  await click(button("확인 기록 남기기", dialog()!));
  expect(calls[0].input).toMatchObject({ reason_code: "NEEDS_RESEARCH", note: "" });
});

it("covers the other old hypotheses of the same change in one decision, unless the person leaves them out", async () => {
  const first = link(), second = link({ hypothesis_id: "h2" }), elsewhere = link({ hypothesis_id: "h3", subject_id: "C-2" });
  await mount(first, [first, second, elsewhere]);
  await click(button("다시 확인"));
  expect(dialog()!.textContent).toContain("다른 가설 1개도 함께 확인");
  await choose("판정이 바뀌었지만 이 가설과 관계없음");
  await click(button("확인 기록 남기기", dialog()!));
  expect(calls[0].input.hypothesis_ids).toEqual(["h1", "h2"]);
  await act(async () => root!.unmount()); container.remove(); root = undefined; document.body.innerHTML = ""; calls.length = 0;
  await mount(first, [first, second]);
  await click(button("다시 확인"));
  await click(dialog()!.querySelector('input[type="checkbox"]'));
  await choose("판정이 바뀌었지만 이 가설과 관계없음");
  await click(button("확인 기록 남기기", dialog()!));
  expect(calls[0].input.hypothesis_ids).toEqual(["h1"]);
});

it("shows a kept hypothesis as rechecked with who and why, and a research request as still old", async () => {
  const event = { reason_code: "STILL_MATCHES" as const, note: "원자료로 확인", actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00", flipped: false };
  await mount(link({ state: "RECHECKED", recheck: event }));
  expect(container.textContent).toContain("다시 확인됨 — 새 결과도 이 가설과 맞음(근거 확인함)");
  expect(container.textContent).toContain("원자료로 확인");
  expect(container.textContent).not.toContain("이전 근거 기준");
  expect(button("다시 확인")).toBeUndefined();
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  await mount(link({ recheck: { ...event, reason_code: "NEEDS_RESEARCH", note: "" } }));
  expect(container.textContent).toContain("이전 근거 기준 — 판정이 바뀌었습니다");
  expect(container.textContent).toContain("이유 기록: 다시 조사가 필요함");
  expect(button("다시 확인")).toBeTruthy();
});

it("says in the dialog that only the basis changed when the verdict state is the same, and not for any other change", async () => {
  await mount(link({ change: "EVIDENCE_ONLY", link_state: "PASS_COMPUTED", current_state: "PASS_COMPUTED" }));
  await click(button("다시 확인"));
  expect(dialog()!.textContent).toContain("판정 상태는 그대로이고, 판정이 기댄 결과나 근거 위치만 바뀌었습니다");
  expect(dialog()!.textContent).not.toMatch(BANNED);
  await act(async () => root!.unmount()); container.remove(); document.body.innerHTML = ""; root = undefined;
  await mount(link());
  await click(button("다시 확인"));
  expect(dialog()!.textContent).not.toContain("판정 상태는 그대로이고");
});
