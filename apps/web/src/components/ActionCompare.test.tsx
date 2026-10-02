import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { ActionCompare } from "./ActionCompare";
import { actionFamilyLabel } from "./statusLabels";

const render = (patch: (result: Record<string, unknown>) => void = () => {}) => {
  const result = structuredClone(excerpt) as Record<string, unknown>;
  patch(result);
  return renderToStaticMarkup(<ActionCompare result={result}/>);
};
// Internal ids may sit in title attributes and technical details only.
const visible = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "").replace(/ title="[^"]*"/g, "");
const analysis = excerpt.action_plan.decision_analysis;

it("leads with the model's recommendation, its uncertainty and what would change it", () => {
  const html = render();
  expect(html.indexOf("추천")).toBeLessThan(html.indexOf("필수 기준"));
  expect(html).toContain(analysis.decision);
  expect(html).toContain(analysis.uncertainty);
  expect(html).toContain(analysis.sensitivity);
});

it("lists mandatory criteria and the action-by-criterion assessments with source numbers", () => {
  const html = render();
  for (const criterion of analysis.criteria) {
    expect(html).toContain(criterion.name);
    expect(html).toContain("필수");
  }
  for (const evaluation of analysis.evaluations) expect(html).toContain(evaluation.assessment);
  expect(html).toMatch(/\[1\]/);
  expect(visible(html)).not.toMatch(/span:|criterion:|action:object/);
});

it("shows risk, execution authority, reversibility and unknown cost and time in plain Korean", () => {
  const html = render();
  for (const text of ["자동 실행 가능", "사전 허용 범위", "격리 실행만", "되돌릴 수 있음"]) expect(html).toContain(text);
  expect(html).toContain("비용·시간: 미확인");
  expect(visible(html)).not.toMatch(/AUTO_R0|PREAUTHORIZED|SANDBOX_ONLY|FULL|READ_ONLY_ANALYSIS/);
  expect(actionFamilyLabel("SANDBOX_REPLAY")).toBe("격리 환경 재현");
  expect(actionFamilyLabel("SOMETHING_ELSE")).toBeNull();
});

const estimate = (dimension: string, band: string, type: string, basis: string, at = "2026-09-30T00:00:00Z") =>
  ({ dimension, band, estimator_type: type, estimator_ref: type === "AI" ? "ACTION_PLANNER" : "human:local-user", basis_text: basis, assumptions: [], created_at: at });
const withEstimates = (list: unknown[]) => render(result => {
  ((result.action_plan as { alternatives: Record<string, unknown>[] }).alternatives[0]).effort_estimates = list;
});

it("replaces the unknown cost and time with the band, who estimated it and why", () => {
  const html = withEstimates([estimate("TIME", "MEDIUM", "AI", "담당자 회신이 필요"), estimate("COST_EFFORT", "LOW", "AI", "기존 자료 재대조")]);
  expect(html).toContain("시간: 보통 · AI 추정 · 담당자 회신이 필요");
  expect(html).toContain("비용·품: 낮음 · AI 추정 · 기존 자료 재대조");
  expect(visible(html)).not.toMatch(/MEDIUM|COST_EFFORT|ACTION_PLANNER/);
});

it("puts an unknown band in the needs-confirmation wording, never as a cheap option", () => {
  const html = withEstimates([estimate("TIME", "UNKNOWN", "AI", "")]);
  expect(html).toContain("시간: 미확인 · 확인 필요");
  expect(html).not.toMatch(/시간: (짧음|낮음)/);
});

it("shows the AI and the person side by side when they differ, and keeps the AI value", () => {
  const html = withEstimates([estimate("TIME", "MEDIUM", "AI", "담당자 회신"), estimate("TIME", "HIGH", "HUMAN", "현장 시험이 필요", "2026-09-30T01:00:00Z")]);
  expect(html).toContain("AI 추정: 보통");
  expect(html).toContain("사람 추정: 김");
  expect(html).toContain("현장 시험이 필요");
});

it("shows one value when the AI and the person agree, and never a number for a band", () => {
  const html = withEstimates([estimate("TIME", "HIGH", "AI", "시험 일정"), estimate("TIME", "HIGH", "HUMAN", "동의", "2026-09-30T01:00:00Z")]);
  expect(html).toContain("시간: 김 · AI·사람 추정 일치");
  expect(html).not.toMatch(/시간: [0-9]/);
});

it("never shows a total score or rank", () => {
  expect(render()).not.toMatch(/총점|순위|1위|점수/);
});

it("asks the user's preference question when the model recorded one", () => {
  const html = render(result => {
    const plan = result.action_plan as { decision_analysis: Record<string, unknown> };
    plan.decision_analysis.preference_question = "비용과 속도 중 무엇이 더 중요합니까?";
  });
  expect(html).toContain("확인하고 싶은 점");
  expect(html).toContain("비용과 속도 중 무엇이 더 중요합니까?");
});

it("still lists the actions when no decision analysis was recorded", () => {
  const html = render(result => { delete (result.action_plan as Record<string, unknown>).decision_analysis; });
  expect(html).not.toContain("필수 기준");
  expect(html).toContain("비용·시간: 미확인");
  expect(render(result => { (result as Record<string, unknown>).action_plan = { alternatives: [] }; })).toContain("아직 제안된 행동이 없습니다");
});
