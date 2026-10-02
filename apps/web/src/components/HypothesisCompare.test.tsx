// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { hypothesisRows } from "./hypothesisView";
import { HypothesisCompare } from "./HypothesisCompare";

let root: Root | undefined;
let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });

const supportUnion = Array.from(new Set(excerpt.portfolio.hypotheses.flatMap(h => h.support_evidence_refs)));
const extra = ["span:extra-0001", "span:extra-0002", "span:extra-0003", "span:extra-0004"];
const selected = [...supportUnion, ...extra];
const result = (patch: (hyps: Record<string, unknown>[]) => void = () => {}) => {
  const hyps = structuredClone(excerpt.portfolio.hypotheses) as Record<string, unknown>[];
  patch(hyps);
  return { answer: excerpt.answer, portfolio: { hypotheses: hyps }, selected_evidence_refs: selected } as Record<string, unknown>;
};
async function mount(value: Record<string, unknown>) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<HypothesisCompare result={value}/>));
}
const visible = () => node.cloneNode(true) as HTMLElement;
const cards = () => Array.from(node.querySelectorAll<HTMLElement>(".hypothesis-card"));

it("reads real IRIS support links (support_evidence_refs) and counts them with the unevaluated rest", () => {
  const rows = hypothesisRows(result());
  expect(rows.map(row => row.support.length)).toEqual([3, 9, 5]);
  expect(rows[0].counter).toEqual([]);
  expect(rows[0].unevaluated).toBe(selected.length - 3);
});

it("merges evidence_refs with the older support field and counts each source once", () => {
  const rows = hypothesisRows(result(hyps => {
    hyps[0].evidence_refs = [supportUnion[0], "span:extra-0001"];
    hyps[1].evidence_refs = ["span:extra-0002"]; delete hyps[1].support_evidence_refs;
    hyps[2].counterevidence_refs = ["span:extra-0003", supportUnion[5]];
  }));
  expect(rows[0].support).toHaveLength(4);
  expect(rows[1].support).toEqual(["span:extra-0002"]);
  expect(rows[2].counter).toEqual(["span:extra-0003", supportUnion[5]]);
});

it("shows counts, the counter-evidence search state and no internal ids on the cards", async () => {
  await mount(result(hyps => { hyps[1].critical_review = { terminal: "UNRESOLVED_NO_RESULTS", reasons: ["x"] }; }));
  const [first, second] = cards();
  expect(first.textContent).toContain(`뒷받침 3 · 반박 - · 미평가 ${selected.length - 3}`);
  expect(first.textContent).toContain("반대 근거 탐색 안 함");
  expect(second.textContent).toContain("반대 근거 탐색에서 결과가 없어 미해결");
  expect(second.textContent).not.toContain("반대 근거 탐색 안 함");
  expect(second.textContent).toContain("반박 0"); // a finished search with nothing found is a real zero
  const clone = visible(); clone.querySelectorAll("details").forEach(item => item.remove());
  expect(clone.textContent).not.toMatch(/span:|hypothesis:/);
  expect(node.textContent).toContain("반대 근거를 찾을 질문");
});

it("filters the relation table to counter and unevaluated cells by default", async () => {
  await mount(result());
  const table = node.querySelector("table.relation-table")!;
  const summary = node.querySelector(".relation-summary")!.textContent!;
  expect(summary).toBe(`표시 ${selected.length}/전체 ${selected.length}`);
  const labels = Array.from(table.querySelectorAll("tbody td .bp6-tag, tbody td .relation-cell")).map(item => item.textContent);
  expect(labels).toContain("지지");
  expect(labels).toContain("미평가");
  const all = Array.from(node.querySelectorAll<HTMLButtonElement>("button")).find(button => button.textContent === "전체 보기")!;
  await act(async () => all.click());
  expect(node.querySelector(".relation-summary")!.textContent).toBe(`표시 ${selected.length}/전체 ${selected.length}`);
});

it("hides fully supported rows under the default filter and shows a counter cell", async () => {
  // one source supports every hypothesis; another is countered by the first hypothesis
  const shared = "span:shared-0001";
  const value = result(hyps => {
    for (const hyp of hyps) hyp.support_evidence_refs = [shared];
    hyps[0].counterevidence_refs = ["span:extra-0001"];
  });
  value.selected_evidence_refs = [shared, "span:extra-0001"];
  await mount(value);
  expect(node.querySelector(".relation-summary")!.textContent).toBe("표시 1/전체 2");
  expect(node.querySelector("table.relation-table")!.textContent).toContain("반박");
  const all = Array.from(node.querySelectorAll<HTMLButtonElement>("button")).find(button => button.textContent === "전체 보기")!;
  await act(async () => all.click());
  expect(node.querySelector(".relation-summary")!.textContent).toBe("표시 2/전체 2");
});

it("adds review buttons to cards and relation cells only when review controls are given, and calls them with the target", async () => {
  await mount(result());
  expect(node.textContent).not.toContain("재검토 요청");
  await act(async () => root!.unmount()); node.remove();
  const asked: { hypothesisId: string; evidenceRef: string | null; evidenceLabel: string }[] = [];
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<HypothesisCompare result={result()} review={{ requests: [], onResend: () => undefined, onRequest: target => asked.push(target) }}/>));
  const all = Array.from(node.querySelectorAll<HTMLButtonElement>("button")).find(button => button.textContent === "전체 보기")!;
  await act(async () => all.click());
  const cell = node.querySelector<HTMLButtonElement>("table.relation-table button.cell-review")!;
  expect(cell.getAttribute("aria-label")).toMatch(/^재검토 요청 · 가설 \d+ · (근거 \[\d+\]|선택 근거 \d+)$/);
  await act(async () => cell.click());
  expect(asked[0].evidenceRef).not.toBeNull();
  const card = Array.from(node.querySelectorAll<HTMLButtonElement>(".hypothesis-card button")).find(button => button.textContent === "재검토 요청")!;
  await act(async () => card.click());
  expect(asked[1]).toMatchObject({ evidenceRef: null, evidenceLabel: "가설 전체" });
});

it("omits the unevaluated count when the answer's selected evidence list is unknown", async () => {
  const value = result(); delete value.selected_evidence_refs;
  await mount(value);
  expect(cards()[0].textContent).toContain("뒷받침 3 · 반박 -");
  expect(cards()[0].textContent).not.toContain("미평가");
  expect(node.querySelector("table.relation-table")).toBeNull();
});
