// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { DiscriminationItem } from "../api/judgmentRecords";
import { ActionCompare } from "./ActionCompare";
import { HypothesisCompare } from "./HypothesisCompare";
import { standingActionNote, standingLine } from "./judgmentRecordText";
import { TestActionDraft } from "./TestActionDraft";

const calls = vi.hoisted(() => [] as string[]);
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    calls.push(method);
    if (method === "action/plan/read") return { value: { plan: { plan_id: input.plan_id }, authorizations: [] }, state: "SUCCEEDED", operation_id: "" };
    return { value: { action: { risk_tier: "R3", policy_state: "APPROVAL_REQUIRED", required_roles: ["project-owner"] }, items: [] }, state: "SUCCEEDED", operation_id: "" };
  } }));

const AGAINST = "이 행동이 기대는 가설이 기록된 시험 결과에서 다른 설명과 맞았습니다";
const item = (id: string, standing: DiscriminationItem["standing"]): DiscriminationItem => ({
  hypothesis_id: id, results: [], result_history_count: 0, refutation_conditions: [], conditions_history_count: 0, elimination: null, standing });
let root: Root | undefined; let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); calls.length = 0; });
async function mount(element: ReactElement) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>));
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise(done => setTimeout(done, 5)); });
}

it("names each standing in plain words and says nothing when there is none", () => {
  expect([undefined, "NONE", "FITS", "AGAINST_ONCE", "AGAINST_REPEATED", "MIXED"].map(value => standingLine(value as never))).toEqual([
    null, null, "기록된 시험 결과: 이 가설과 맞음", "기록된 시험 결과: 다른 설명과 맞음(1건)", "기록된 시험 결과: 다른 설명과 맞음(반복)", "기록된 시험 결과: 엇갈림"]);
});

it("notes an action only when a hypothesis it rests on went against the recorded results", () => {
  const items = [item("h-1", "AGAINST_ONCE"), item("h-2", "AGAINST_REPEATED"), item("h-3", "FITS"), item("h-4", "MIXED")];
  expect(standingActionNote(["h-1"], items)).toContain(AGAINST);
  expect(standingActionNote(["h-3", "h-2"], items)).toContain(AGAINST);
  for (const ids of [["h-3"], ["h-4"], ["h-9"], []]) expect(standingActionNote(ids, items)).toBeNull();
  expect(standingActionNote(["h-1"], undefined)).toBeNull();
  expect(standingActionNote(["h-1"], items)).toContain("막지 않습니다");
});

const hypothesisResult = () => structuredClone(excerpt) as unknown as Record<string, unknown>;
const firstId = () => (hypothesisResult().portfolio as { hypotheses: { hypothesis_id: string }[] }).hypotheses[0].hypothesis_id;

it("shows the recorded-results line on a hypothesis card, apart from its other status, and none when nothing was recorded", async () => {
  await mount(<HypothesisCompare result={hypothesisResult()} projectId="p" canDraft discrimination={[item(firstId(), "AGAINST_REPEATED")]}/>);
  const card = node.querySelectorAll(".hypothesis-card")[0];
  expect(card.textContent).toContain("기록된 시험 결과: 다른 설명과 맞음(반복)");
  expect(card.textContent).toContain("봉인된 시험");
  expect(card.textContent).not.toMatch(/AGAINST_|FITS|MIXED/);
  const second = node.querySelectorAll(".hypothesis-card")[1];
  expect(second.textContent).not.toContain("기록된 시험 결과");
});

it("does not hold back the draft button when the hypothesis went against the recorded results", async () => {
  await mount(<TestActionDraft projectId="p" hypothesisId="h-1" testId="t1" standing="AGAINST_ONCE"/>);
  expect(node.textContent).toContain(AGAINST);
  const button = [...node.querySelectorAll("button")].find(b => b.textContent?.includes("행동 요청 준비")) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  await act(async () => button.click());
  await act(async () => { await new Promise(done => setTimeout(done, 5)); });
  expect(calls).toContain("action/draft/fromTest");
  await act(async () => root!.unmount()); node.remove();
  await mount(<TestActionDraft projectId="p" hypothesisId="h-1" testId="t1" standing="FITS"/>);
  expect(node.textContent).not.toContain(AGAINST);
});

const planResult = () => {
  const result = structuredClone(excerpt) as unknown as { action_plan: { plan_id?: string; alternatives: Record<string, unknown>[] } };
  result.action_plan.plan_id = "plan:1";
  result.action_plan.alternatives[0].hypothesis_ids = ["h-1"];
  result.action_plan.alternatives[0].execution_authority = "HUMAN_REQUIRED_R3";
  result.action_plan.alternatives[1].hypothesis_ids = ["h-3"];
  return result as unknown as Record<string, unknown>;
};

it("marks the action card and the approval place, and still lets the approval be read as before", async () => {
  await mount(<ActionCompare result={planResult()} projectId="p" discrimination={[item("h-1", "AGAINST_ONCE"), item("h-3", "FITS")]}/>);
  const cards = [...node.querySelectorAll(".action-card")];
  expect(cards.filter(card => card.textContent?.includes(AGAINST))).toHaveLength(1);
  expect(node.querySelectorAll(".standing-approval-note")).toHaveLength(1);
  expect(calls).toContain("action/plan/read"); // the approval is read as it always was
});

it("shows no note on the action cards when no record is known", () => {
  const html = renderToStaticMarkup(<ActionCompare result={planResult()}/>);
  expect(html).not.toContain(AGAINST);
});
