import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { HypothesisLink } from "../api/hypothesisLink";
import { ActionCompare } from "./ActionCompare";

const link = (id: string, patch: Partial<HypothesisLink> = {}): HypothesisLink => ({
  hypothesis_id: id, hypothesis_revision_digest: "d".repeat(64), statement: "s", subject_kind: "CRITERION", subject_id: "C-1", subject_title: "비 조건 탐지율",
  state: "STALE", change: "CONTENT_CHANGED", link_state: "HOLD_NO_RESULT", current_state: "FAIL_COMPUTED", current_verdict_revision: "r".repeat(64), recheck: null, ...patch,
});
// The first action rests on h-1, the second on h-2, the third on nothing that has a link.
const render = (links?: HypothesisLink[]) => {
  const result = structuredClone(excerpt) as Record<string, unknown>;
  const alternatives = (result.action_plan as { alternatives: Record<string, unknown>[] }).alternatives;
  alternatives[0].hypothesis_ids = ["h-1"];
  alternatives[1].hypothesis_ids = ["h-2"];
  return renderToStaticMarkup(<ActionCompare result={result} links={links}/>);
};
const NOTE = "이 행동이 기대는 가설";
const cards = (html: string) => html.split('<section class="detail-card action-card"').slice(1);

it("marks only the action that rests on a hypothesis whose verdict changed", () => {
  const found = cards(render([link("h-1"), link("h-2", { state: "CURRENT", change: null })]));
  expect(found).toHaveLength(3);
  expect(found[0]).toContain(NOTE);
  expect(found[0]).toContain("승인 요청과 결정, 실행이 막힙니다");
  expect(found[1]).not.toContain(NOTE); // its hypothesis is current
  expect(found[2]).not.toContain(NOTE); // no hypothesis with a link: not held back
});

it("says a changed basis under the same verdict apart, and shows nothing once a person kept the link", () => {
  const found = cards(render([link("h-1", { change: "EVIDENCE_ONLY", link_state: "PASS_COMPUTED", current_state: "PASS_COMPUTED" }), link("h-2", { state: "RECHECKED" })]));
  expect(found[0]).toContain("판정 상태는 같고 근거만 바뀜");
  expect(found[1]).not.toContain(NOTE);
});

it("shows no mark when the links are not known", () => {
  expect(render(undefined)).not.toContain(NOTE);
  expect(render([])).not.toContain(NOTE);
});
