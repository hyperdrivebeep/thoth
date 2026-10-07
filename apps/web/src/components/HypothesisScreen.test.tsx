// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { HypothesisCompare } from "./HypothesisCompare";
import { causeUnconfirmed } from "./hypothesisReview";
import { hypothesisRows, relationOf } from "./hypothesisView";
import { matrixLines } from "./hypothesisMatrix";
import { ResearchResultCard } from "./ResearchResultCard";

let root: Root | undefined;
let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });

type Decision = { hypothesis_id: string; relation: string; evidence_refs?: string[]; explanation?: string; gaps?: string[] };
const ids = excerpt.portfolio.hypotheses.map(h => h.hypothesis_id);
const supportUnion = Array.from(new Set(excerpt.portfolio.hypotheses.flatMap(h => h.support_evidence_refs)));
const build = (decisions: Decision[] | null, patch: (hyps: Record<string, unknown>[]) => void = () => {}) => {
  const hyps = structuredClone(excerpt.portfolio.hypotheses) as Record<string, unknown>[];
  patch(hyps);
  const value: Record<string, unknown> = { answer: excerpt.answer, portfolio: { hypotheses: hyps }, selected_evidence_refs: supportUnion };
  if (decisions) value.hypothesis_review = { decisions, alternatives_considered: [], next_checks: [], uncertainty_reserve: "" };
  return value;
};
const review = (relations: string[]): Decision[] => relations.map((relation, index) => ({ hypothesis_id: ids[index], relation, explanation: "검토한 설명입니다.", gaps: ["더 필요한 자료가 있습니다."] }));
async function mount(value: Record<string, unknown>) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<HypothesisCompare result={value}/>));
}
const NOTICE = "원인 미확인 — 지지된 가설이 없습니다(원인이 없다는 뜻이 아닙니다)";
/** The IRIS fixture carries model-written sentences and its own missing-evidence lists; strip them to leave only what the screen words itself. */
const plain = (hyps: Record<string, unknown>[]) => {
  for (const hyp of hyps) {
    Object.assign(hyp, { statement: "가설 문장입니다.", uncertainty: "아직 불확실합니다.", missing_evidence: [], counterevidence_queries: [],
      discriminating_tests: [{ procedure_candidate: "시험 절차입니다.", expected_if_true: "참일 때 보입니다.", expected_if_alternative: "다른 설명일 때 보입니다." }] });
  }
};

it("splits the observation from the inference and shows prediction, missing evidence and the dropping signal in words", async () => {
  await mount(build(review(["SUPPORTED", "QUALIFIED", "INCONCLUSIVE"]), hyps => {
    hyps[0].observed_problem = "비 오는 날 제동 거리가 길어집니다.";
    hyps[0].assumptions = ["노면 상태가 같았습니다."];
    hyps[0].predicted_observations = ["젖은 노면에서만 거리가 늘어납니다."];
    hyps[0].missing_evidence = ["타이어 마모 기록"];
    hyps[0].status = "TESTABLE";
  }));
  const first = node.querySelector<HTMLElement>(".hypothesis-card")!;
  expect(first.querySelector(".hypothesis-observed")!.textContent).toContain("비 오는 날 제동 거리가 길어집니다.");
  expect(first.querySelector(".hypothesis-inferred")!.textContent).toContain(String(hyps0Statement()));
  expect(first.textContent).toContain("노면 상태가 같았습니다.");
  expect(first.textContent).toContain("젖은 노면에서만 거리가 늘어납니다.");
  expect(first.textContent).toContain("타이어 마모 기록");
  expect(first.textContent).toContain("시험할 수 있음");
  expect(first.textContent).toContain("이 가설을 버릴 신호");
  const words = Array.from(node.querySelectorAll(".hypothesis-card")).map(card => card.querySelector(".hypothesis-review")?.textContent ?? "");
  expect(words[0]).toContain("지지");
  expect(words[1]).toContain("조건부 지지");
  expect(words[2]).toContain("결론 불가");
  expect(words[0]).toContain("검토한 설명입니다.");
});
const hyps0Statement = () => excerpt.portfolio.hypotheses[0].statement;

it("shows the cause-unconfirmed line only when a review exists and none of its decisions supports a hypothesis", async () => {
  await mount(build(review(["UNSUPPORTED", "INCONCLUSIVE", "INCONCLUSIVE"])));
  expect(node.textContent).toContain(NOTICE);
  expect(node.textContent!.indexOf(NOTICE)).toBeLessThan(node.textContent!.indexOf(hyps0Statement()));
  expect(causeUnconfirmed(build(review(["UNSUPPORTED", "INCONCLUSIVE"])))).toBe(true);
  expect(causeUnconfirmed(build(review(["UNSUPPORTED", "QUALIFIED"])))).toBe(false); // one conditional support is enough
  expect(causeUnconfirmed(build(review(["SUPPORTED", "INCONCLUSIVE"])))).toBe(false);
  expect(causeUnconfirmed(build(null))).toBe(false); // no review recorded: say nothing instead of guessing
  expect(causeUnconfirmed(build([]))).toBe(false);
});

it("does not show the cause-unconfirmed line for a supported or an unreviewed old record", async () => {
  await mount(build(review(["INCONCLUSIVE", "SUPPORTED", "UNSUPPORTED"])));
  expect(node.textContent).not.toContain("원인 미확인");
  await act(async () => root!.unmount()); node.remove();
  await mount(build(null));
  expect(node.textContent).not.toContain("원인 미확인");
});

it("also shows the cause-unconfirmed line in the result card summary by the same rule", async () => {
  const render = async (value: Record<string, unknown>) => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    node = document.createElement("div"); document.body.append(node); root = createRoot(node);
    await act(async () => root!.render(<ResearchResultCard result={value} state="COMPLETED" onDetail={() => undefined}/>));
  };
  await render(build(review(["INCONCLUSIVE", "UNSUPPORTED", "INCONCLUSIVE"])));
  expect(node.querySelector(".conversation-findings")!.textContent).toContain(NOTICE);
  await act(async () => root!.unmount()); node.remove();
  await render(build(review(["QUALIFIED", "UNSUPPORTED", "INCONCLUSIVE"])));
  expect(node.querySelector(".conversation-findings")!.textContent).not.toContain("원인 미확인");
});

it("writes unevaluated cells as text, marks a row that tells hypotheses apart, and never scores", async () => {
  // hypothesis 1 supports the shared source, hypothesis 2 counters it, hypothesis 3 has not looked at it
  const shared = "span:shared-0001";
  const value = build(null, hyps => {
    hyps[0].support_evidence_refs = [shared]; hyps[1].support_evidence_refs = []; hyps[1].counterevidence_refs = [shared]; hyps[2].support_evidence_refs = [];
  });
  value.selected_evidence_refs = [shared, "span:other-0001"];
  const lines = matrixLines(hypothesisRows(value), value.selected_evidence_refs as string[]);
  expect(lines[0].cells).toEqual(["SUPPORT", "COUNTER", "UNEVALUATED"]);
  expect(lines[0].splits).toBe(true);
  expect(lines[1].cells).toEqual(["UNEVALUATED", "UNEVALUATED", "UNEVALUATED"]);
  expect(lines[1].splits).toBe(false); // nothing was evaluated, so nothing was told apart
  await mount(value);
  const rows = Array.from(node.querySelectorAll("table.relation-table tbody tr"));
  expect(rows[0].textContent).toContain("가설을 가름");
  expect(rows[0].textContent).toContain("미평가"); // written as a word, not an empty cell
  expect(rows[1].textContent).not.toContain("가설을 가름");
  expect(Array.from(rows[1].querySelectorAll("td")).slice(1).every(cell => cell.textContent!.includes("미평가"))).toBe(true);
});

it("does not mark a row as telling hypotheses apart when only one is evaluated and the rest are not", () => {
  const value = build(null, hyps => { hyps[0].support_evidence_refs = ["span:a"]; hyps[1].support_evidence_refs = []; hyps[2].support_evidence_refs = []; });
  const lines = matrixLines(hypothesisRows(value), ["span:a"]);
  expect(lines[0].splits).toBe(false);
});

it("treats a source the review cited and judged unsupportive as not applicable, not as supportive", () => {
  const value = build([{ hypothesis_id: ids[1], relation: "UNSUPPORTED", evidence_refs: ["span:a"], explanation: "관계가 없습니다." }], hyps => {
    hyps[0].support_evidence_refs = ["span:a"]; hyps[1].support_evidence_refs = []; hyps[2].support_evidence_refs = [];
  });
  const rows = hypothesisRows(value);
  expect(relationOf(rows[1], "span:a")).toBe("NOT_APPLICABLE");
  expect(matrixLines(rows, ["span:a"])[0].cells).toEqual(["SUPPORT", "NOT_APPLICABLE", "UNEVALUATED"]);
});

it("says no counter-evidence was found when a finished search found none, without counting it as support", async () => {
  await mount(build(null, hyps => { for (const h of hyps) { h.critical_review = { terminal: "UNRESOLVED_NO_RESULTS", reasons: [] }; h.counterevidence_refs = []; } }));
  expect(node.textContent).toContain("반박 근거를 찾지 못함");
  expect(node.textContent).toContain("지지된다는 뜻은 아닙니다");
  const card = node.querySelector(".hypothesis-card")!.textContent!;
  expect(card).toContain("뒷받침 3");
});

it("renders an old record without the new fields without breaking, and hides what it does not have", async () => {
  await mount(build(null, plain));
  const first = node.querySelector<HTMLElement>(".hypothesis-card")!;
  expect(first.querySelector(".hypothesis-observed")!.textContent).toContain("기록 없음");
  expect(first.querySelector(".hypothesis-review")).toBeNull();
  expect(first.textContent).not.toContain("가정");
  expect(first.textContent).not.toContain("모자란 근거");
  expect(node.textContent).not.toMatch(/undefined|null|NaN/);
});

it("uses plain words only: no internal code, no English field name, no probability number, no forbidden claim", async () => {
  await mount(build(review(["UNSUPPORTED", "INCONCLUSIVE", "QUALIFIED"]), hyps => { plain(hyps); hyps[0].status = "SOMETHING_NEW"; hyps[0].observed_problem = "문제가 보입니다."; }));
  const clone = node.cloneNode(true) as HTMLElement; clone.querySelectorAll("details").forEach(item => item.remove());
  const text = clone.textContent!;
  expect(text).not.toMatch(/SUPPORTED|QUALIFIED|UNSUPPORTED|INCONCLUSIVE|SOMETHING_NEW|NOT_APPLICABLE|UNEVALUATED|observed_problem|hypothesis_review/);
  expect(text).not.toMatch(/\d+(\.\d+)?\s*%|확률|가능성이 \d/);
  expect(text).not.toMatch(/원인은|확정|해결됨|통과 가능|승인됨/);
});
