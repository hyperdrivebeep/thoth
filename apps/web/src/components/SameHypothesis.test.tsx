// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { HypothesisLink } from "../api/hypothesisLink";
import type { SameView } from "../api/hypothesisSame";
import type { DiscriminationItem } from "../api/judgmentRecords";
import type { LessonItem } from "../api/lessons";
import { LessonRecall } from "./LessonRecall";
import { SameHypothesis } from "./SameHypothesis";

const calls = vi.hoisted(() => [] as { method: string; input: Record<string, unknown> }[]);
const fail = vi.hoisted(() => ({ message: "" }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    calls.push({ method, input });
    if (fail.message) throw new Error(fail.message);
    return { value: { groups: [], members: {}, events: [] }, state: "SUCCEEDED", operation_id: "op" };
  } }));

const BANNED = /통과|해결|승인 완료|검증 완료/;
const LEAK = /SAME_|LINK|UNLINK|AGAINST_|FITS|MIXED|undefined|\bnull\b|hypothesis:/;
const NOTE = "사람이 같은 가설이라고 표시한 것입니다. 이전 조사의 결과는 참고로만 보이고, 이 가설의 배제나 시험 순서에 더해지지 않습니다.";
const link = (id: string, patch: Partial<HypothesisLink> = {}): HypothesisLink => ({
  hypothesis_id: id, hypothesis_revision_digest: "d".repeat(64), statement: `가설 문장 ${id}`, subject_kind: "CRITERION", subject_id: "C-1", subject_title: "비 조건 탐지율",
  state: "CURRENT", change: null, link_state: "FAIL_COMPUTED", current_state: "FAIL_COMPUTED", current_verdict_revision: "r".repeat(64), recheck: null, ...patch,
});
const none: SameView = { groups: [], pairs: [], members: {}, events: [] };
const grouped: SameView = { groups: [["h-1", "h-2"]], pairs: [["h-1", "h-2"]], members: {
  "h-1": { statement: "가설 문장 h-1", investigated_at: "2026-10-01T01:02:03+00:00", subject_kind: "CRITERION", subject_id: "C-1" },
  "h-2": { statement: "가설 문장 h-2", investigated_at: "2026-10-05T01:02:03+00:00", subject_kind: "CRITERION", subject_id: "C-1" } }, events: [] };
const standing = (id: string, value: DiscriminationItem["standing"]): DiscriminationItem => ({ hypothesis_id: id, results: [], result_history_count: 0, refutation_conditions: [], conditions_history_count: 0, elimination: null, standing: value });

let root: Root | undefined; let node: HTMLDivElement;
beforeEach(() => { calls.length = 0; fail.message = ""; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); document.body.innerHTML = ""; });
async function mount(element: ReactElement) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>));
}
const settle = () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
const press = async (label: string) => { await act(async () => [...document.body.querySelectorAll("button")].find(b => b.textContent?.trim() === label)!.click()); await settle(); };

it("offers to mark a hypothesis as the same and lists the other hypotheses of the project, the same row first", async () => {
  const candidates = [link("h-9", { subject_id: "C-2", subject_title: "다른 줄" }), link("h-2"), link("h-1")];
  await mount(<SameHypothesis projectId="p" hypothesisId="h-1" same={none} candidates={candidates}/>);
  expect(node.textContent).toContain("다른 조사의 같은 가설로 표시");
  expect(calls).toHaveLength(0);
  await press("다른 조사의 같은 가설로 표시");
  const options = [...node.querySelectorAll("input[type=radio]")].map(input => input.closest("label")?.textContent ?? "");
  expect(options).toHaveLength(2);
  expect(options.some(text => text.includes("h-1"))).toBe(false); // not itself
  expect(options[0]).toContain("가설 문장 h-2"); // the same trace row comes first
  expect(options[0]).toContain("비 조건 탐지율");
  expect(options[1]).toContain("가설 문장 h-9");
  expect(node.textContent).not.toMatch(LEAK);
});

it("sends the mark only when a person picks one, with the note they wrote", async () => {
  await mount(<SameHypothesis projectId="p" hypothesisId="h-1" same={none} candidates={[link("h-2")]}/>);
  await press("다른 조사의 같은 가설로 표시");
  expect([...node.querySelectorAll("button")].find(b => b.textContent?.trim() === "표시하기")!.disabled).toBe(true);
  await act(async () => node.querySelector<HTMLInputElement>("input[type=radio]")!.click());
  const memo = node.querySelector("textarea, input[type=text]") as HTMLTextAreaElement;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(memo, "같은 줄을 다시 조사함"); memo.dispatchEvent(new Event("input", { bubbles: true })); });
  await press("표시하기");
  expect(calls[0]).toEqual({ method: "hypothesis/same/record", input: { project_id: "p", hypothesis_ids: ["h-1", "h-2"], action: "LINK", note: "같은 줄을 다시 조사함" } });
});

it("shows the other hypothesis of the group with its name, date and recorded results, and the note that it is only a reference", async () => {
  await mount(<SameHypothesis projectId="p" hypothesisId="h-1" same={grouped} candidates={[link("h-1"), link("h-2")]} discrimination={[standing("h-2", "AGAINST_REPEATED")]}/>);
  const text = node.textContent!;
  expect(text).toContain("가설 문장 h-2");
  expect(text).toContain("기록된 시험 결과: 다른 설명과 맞음(반복)");
  expect(text).toContain("조사");
  expect(text).toContain(NOTE);
  expect(text).not.toContain("가설 문장 h-1"); // only the others are listed
  expect(text).not.toMatch(LEAK);
  expect(text).not.toMatch(BANNED);
});

it("says no results were recorded when there are none, and takes the mark back only on the button", async () => {
  await mount(<SameHypothesis projectId="p" hypothesisId="h-1" same={grouped} candidates={[]}/>);
  expect(node.textContent).toContain("기록된 시험 결과 없음");
  expect(calls).toHaveLength(0);
  await press("연결 끊기");
  expect(calls[0]).toEqual({ method: "hypothesis/same/record", input: { project_id: "p", hypothesis_ids: ["h-1", "h-2"], action: "UNLINK", note: "" } });
});

it("offers to take a mark back only on a pair that was linked directly", async () => {
  const chain: SameView = { groups: [["h-1", "h-2", "h-3"]], pairs: [["h-1", "h-2"], ["h-2", "h-3"]], events: [], members: {
    "h-1": { statement: "가설 문장 h-1", investigated_at: "2026-10-01T01:02:03+00:00" },
    "h-2": { statement: "가설 문장 h-2", investigated_at: "2026-10-02T01:02:03+00:00" },
    "h-3": { statement: "가설 문장 h-3", investigated_at: "2026-10-03T01:02:03+00:00" } } };
  await mount(<SameHypothesis projectId="p" hypothesisId="h-1" same={chain} candidates={[]}/>);
  const items = [...node.querySelectorAll(".same-group li")];
  expect(items).toHaveLength(2);
  expect(items[0].textContent).toContain("가설 문장 h-2");
  expect(items[0].textContent).toContain("연결 끊기"); // linked directly
  expect(items[1].textContent).toContain("가설 문장 h-3");
  expect(items[1].textContent).not.toContain("연결 끊기"); // only joined through h-2: that mark is taken back from h-2
  expect(items[1].textContent).toContain("다른 가설을 거쳐 이어져 있습니다");
  await press("연결 끊기");
  expect(calls[0].input).toEqual({ project_id: "p", hypothesis_ids: ["h-1", "h-2"], action: "UNLINK", note: "" });
});

it("names a refusal in plain words", async () => {
  fail.message = "SAME_ALREADY_LINKED";
  await mount(<SameHypothesis projectId="p" hypothesisId="h-1" same={none} candidates={[link("h-2")]}/>);
  await press("다른 조사의 같은 가설로 표시");
  await act(async () => node.querySelector<HTMLInputElement>("input[type=radio]")!.click());
  await press("표시하기");
  expect(node.textContent).toContain("이미 같은 가설로 표시되어 있습니다");
  expect(node.textContent).not.toMatch(LEAK);
});

it("tells the lessons of the same hypothesis from another investigation apart in the recall", async () => {
  const lesson = (id: string, hypothesisId: string, same?: string[]): LessonItem => ({
    lesson_id: id, kind: "TEST_RESULT", outcome: "ALTERNATIVE", state: "SAME_CONDITION", subject_kind: "CRITERION", subject_id: "C-1",
    hypothesis_id: hypothesisId, test_id: "t1", actor_id: "human:local-user", created_at: "2026-10-05T01:02:03+00:00",
    detail: { observations: [{ test_id: "t1", matched: "ALTERNATIVE", observation: "관찰함", evidence_refs: [] }] }, ...(same ? { same_hypothesis_ids: same } : {}) });
  await mount(<LessonRecall links={[link("h-1")]} shownIds={["h-1"]} lessons={[lesson("a", "h-2", ["h-1"]), lesson("b", "h-3")]}/>);
  const items = [...node.querySelectorAll("li")];
  expect(items).toHaveLength(2);
  expect(items.filter(item => item.textContent?.includes("다른 조사의 같은 가설"))).toHaveLength(1);
  expect(items.find(item => item.textContent?.includes("다른 조사의 같은 가설"))!.textContent).toContain("관찰함");
});
