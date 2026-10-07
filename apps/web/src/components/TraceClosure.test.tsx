// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TracePage } from "./TracePage";
import demo from "./traceDemo.fixture.json";

type Json = Record<string, unknown>;
const state = vi.hoisted(() => ({ calls: [] as { method: string; input: Json }[], closures: [] as Json[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Json) => {
    state.calls.push({ method, input });
    const done = (value: unknown) => ({ value, state: "SUCCEEDED", operation_id: "" });
    if (method === "trace/read") return done(demo.phase1);
    if (method === "trace/closure/list") return done({ closures: state.closures });
    if (method === "trace/closure/record") return done({ event: {}, closures: state.closures });
    throw new Error("unexpected " + method);
  } }));

const BANNED = /통과|해결|승인 완료|검증 완료/;
const RAW = ["FIX_APPLIED", "HUMAN_CLOSED", "WAIVER_RECORDED", "CONDITION_CHANGED", "HOLD_NO_RESULT", "FAIL_COMPUTED", "effect_confirmed"];
let root: Root | undefined; let container: HTMLDivElement;
const settle = async () => { for (let i = 0; i < 10; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); }); };
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><TracePage projectId="project:radar"/></QueryClientProvider>));
  await settle();
}
beforeEach(() => { state.calls = []; state.closures = []; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; });

const rowOf = (text: string) => [...container.querySelectorAll("tr")].find(tr => tr.querySelector("th")?.textContent?.includes(text)) as HTMLElement;
const button = (label: string, scope: ParentNode = document.body) => [...scope.querySelectorAll("button")].find(b => (b.getAttribute("aria-label") ?? b.textContent?.trim()) === label) as HTMLButtonElement | undefined;
const click = async (element: Element | undefined | null) => { expect(element).toBeTruthy(); await act(async () => (element as HTMLElement).click()); await settle(); };
const dialog = () => document.body.querySelector<HTMLElement>('[role="dialog"]')!;
const choose = async (label: string) => click([...dialog().querySelectorAll("label")].find(l => l.textContent?.includes(label))!.querySelector("input"));
const write = async (label: string, text: string) => {
  const area = dialog().querySelector<HTMLTextAreaElement | HTMLInputElement>('[aria-label="' + label + '"]')!;
  const proto = area instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(area, text); area.dispatchEvent(new Event("input", { bubbles: true })); });
};
const verdictOf = (id: string) => (demo.phase1.verdicts as { subject_id: string; revision_digest: string }[]).find(item => item.subject_id === id)!;
const closure = (kind: string, extra: Json = {}, row: Json = {}): Json => ({ subject_kind: "CRITERION", subject_id: "SYN-C-DET-RAIN", current_state: "HOLD_NO_RESULT", effect_confirmed: false,
  effect_verdict_revision: null, verdict_changed_since: false, ...row, events: [{ event_id: "e1", subject_kind: "CRITERION", subject_id: "SYN-C-DET-RAIN", kind, basis_ref: "ECN-12", note: "", scope: null,
    verdict_revision: verdictOf("SYN-C-DET-RAIN").revision_digest, verdict_digest: "d", verdict_state: "HOLD_NO_RESULT", actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00", ...extra }] });

it("offers to record a closure on a row that is not met, and not on a met one", async () => {
  await mount();
  expect(button("Detection rate in rain 처분 기록 남기기", rowOf("Detection rate in rain"))).toBeTruthy();
  expect(button("Detection rate in fog 처분 기록 남기기", rowOf("Detection rate in fog"))).toBeTruthy();
  expect(rowOf("Detection rate in dry weather").querySelector('[aria-label$="처분 기록 남기기"]')).toBeNull();
  expect(state.calls.map(call => call.method)).toEqual(["trace/read", "trace/closure/list"]);
});

it("shows a closure beside the verdict and leaves the verdict's words and colour as they were", async () => {
  await mount();
  const tagBefore = rowOf("Detection rate in rain").querySelector(".bp6-tag")!;
  const before = [tagBefore.textContent, tagBefore.className];
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  state.closures = [closure("WAIVER_RECORDED")];
  await mount();
  const rain = rowOf("Detection rate in rain");
  const tag = rain.querySelector(".bp6-tag")!;
  expect([tag.textContent, tag.className]).toEqual(before); // the verdict's tag is untouched
  const mark = rain.querySelector(".trace-closure")!;
  expect(mark.textContent).toContain("편차·면제 승인(원 기준 미충족 유지)");
  expect(mark.textContent).toContain("원래 판정: 보류 · 결과 없음");
  expect(mark.textContent).toContain("근거 문서: ECN-12");
  expect(tag.contains(mark)).toBe(false);
  expect(rowOf("Detection rate in fog").querySelector(".trace-closure")).toBeNull();
});

it("calls the effect confirmed only when the server says the rules confirmed it", async () => {
  state.closures = [closure("FIX_APPLIED")];
  await mount();
  expect(rowOf("Detection rate in rain").querySelector(".trace-closure")!.textContent).toContain("수정 반영됨(효과 미확인)");
  expect(container.textContent).not.toContain("효과 확인됨");
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  state.closures = [closure("FIX_APPLIED", {}, { effect_confirmed: true, effect_verdict_revision: "x" })];
  await mount();
  expect(rowOf("Detection rate in rain").querySelector(".trace-closure")!.textContent).toContain("효과 확인됨");
});

it("needs a kind, the document it rests on, and for a changed condition its range; then sends the verdict it saw", async () => {
  await mount();
  await click(button("Detection rate in rain 처분 기록 남기기"));
  expect(dialog().textContent).toContain("THOTH가 면제나 승인을 내리지 않으며");
  await click(button("기록 남기기", dialog()));
  expect(dialog().textContent).toContain("처분 종류를 고르세요");
  await choose("운용 조건 변경");
  await click(button("기록 남기기", dialog()));
  expect(dialog().textContent).toContain("근거 문서를 적어 주세요");
  await write("근거 문서", " OPS-9 ");
  await click(button("기록 남기기", dialog()));
  expect(dialog().textContent).toContain("해당 조건 범위를 적어 주세요");
  expect(state.calls.some(call => call.method === "trace/closure/record")).toBe(false);
  await write("해당 조건 범위", "50 m 이하 안개");
  await click(button("기록 남기기", dialog()));
  const sent = state.calls.find(call => call.method === "trace/closure/record")!;
  expect(sent.input).toEqual({ project_id: "project:radar", subject_kind: "CRITERION", subject_id: "SYN-C-DET-RAIN", kind: "CONDITION_CHANGED", basis_ref: "OPS-9", note: "",
    scope: "50 m 이하 안개", current_verdict_revision: verdictOf("SYN-C-DET-RAIN").revision_digest });
});

it("sends no scope for the other kinds and shows no forbidden word or code", async () => {
  state.closures = [closure("HUMAN_CLOSED")];
  await mount();
  await click(button("Detection rate in fog 처분 기록 남기기"));
  await choose("수정 반영됨");
  await write("근거 문서", "ECN-3");
  await write("메모", "배선을 바꿈");
  await click(button("기록 남기기", dialog()));
  const sent = state.calls.find(call => call.method === "trace/closure/record")!;
  expect(sent.input).toMatchObject({ subject_id: "SYN-C-DET-FOG", kind: "FIX_APPLIED", basis_ref: "ECN-3", note: "배선을 바꿈" });
  expect("scope" in sent.input).toBe(false);
  const text = container.textContent!;
  expect(text).not.toMatch(BANNED);
  for (const raw of RAW) expect(text).not.toContain(raw);
});
