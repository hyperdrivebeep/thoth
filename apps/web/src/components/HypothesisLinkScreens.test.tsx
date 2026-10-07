// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { HypothesisLink } from "../api/hypothesisLink";
import { HypothesisCompare } from "./HypothesisCompare";
import { ResearchResultCard } from "./ResearchResultCard";

let root: Root | undefined;
let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });

const ids = excerpt.portfolio.hypotheses.map(h => h.hypothesis_id);
const link = (index: number, patch: Partial<HypothesisLink> = {}): HypothesisLink => ({
  hypothesis_id: ids[index], hypothesis_revision_digest: "d".repeat(64), statement: "s", subject_kind: "CRITERION", subject_id: "C-1", subject_title: "비 조건 탐지율",
  state: "STALE", change: "CONTENT_CHANGED", link_state: "HOLD_NO_RESULT", current_state: "FAIL_COMPUTED", current_verdict_revision: "r".repeat(64), recheck: null, ...patch,
});
const result = { answer: excerpt.answer, portfolio: { hypotheses: structuredClone(excerpt.portfolio.hypotheses) }, selected_evidence_refs: [] } as Record<string, unknown>;
async function mount(element: React.ReactElement) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(element));
}
const NOTICE = "이전 근거 기준 — 판정이 바뀌었습니다";

it("marks only the hypothesis whose row changed, on the comparison cards", async () => {
  await mount(<HypothesisCompare result={result} projectId="p" links={[link(0), link(1, { state: "CURRENT", change: null })]}/>);
  const cards = [...node.querySelectorAll<HTMLElement>(".hypothesis-card")];
  expect(cards[0].textContent).toContain(NOTICE);
  expect(cards[1].textContent).not.toContain(NOTICE); // its row did not change
  expect(cards[2].textContent).not.toContain(NOTICE); // no link at all: an old or ordinary hypothesis
});

it("shows no mark when there are no links or no project to ask", async () => {
  await mount(<HypothesisCompare result={result} projectId="p"/>);
  expect(node.textContent).not.toContain(NOTICE);
  await act(async () => root!.unmount()); node.remove(); root = undefined;
  await mount(<HypothesisCompare result={result} links={[link(0)]}/>);
  expect(node.textContent).not.toContain(NOTICE);
});

it("marks the same hypothesis in the result card summary", async () => {
  await mount(<ResearchResultCard result={result} state="COMPLETED" onDetail={() => undefined} projectId="p" hypothesisLinks={[link(2)]}/>);
  const summary = node.querySelector(".conversation-findings")!;
  const rows = [...summary.querySelectorAll(":scope > div")];
  expect(rows[2].textContent).toContain(NOTICE);
  expect(rows[0].textContent).not.toContain(NOTICE);
  expect(summary.querySelectorAll(".hypothesis-link")).toHaveLength(1);
  expect(summary.textContent).toContain("다시 확인");
});
