import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "../i18n/locale";
import { AbsenceChecks } from "./AbsenceChecks";
import { ResearchResultCard } from "./ResearchResultCard";

const payload = { schema_version: "1.0.0", checker_version: "literal-absence:1.0.0", items: [
  { target: "Polars 1.44.1", a_outcome: "FOUND", b_outcome: "FOUND", effective: "FOUND_IN_MODEL_INPUT", warning_code: "FOUND_IN_MODEL_INPUT" },
  { target: "Polars 1.44.2", a_outcome: "NOT_FOUND_WITHIN_SCOPE", b_outcome: "FOUND", effective: "FOUND_OUTSIDE_MODEL_INPUT", warning_code: "FOUND_OUTSIDE_MODEL_INPUT", injection_state: "NOT_DELIVERED" },
  { target: "반복 분포", a_outcome: "UNSUPPORTED", b_outcome: "UNSUPPORTED", effective: "UNSUPPORTED" },
  { target: "Other", a_outcome: "NOT_FOUND_WITHIN_SCOPE", b_outcome: "NOT_FOUND_WITHIN_SCOPE", effective: "NOT_FOUND_WITHIN_SCOPES" },
] };
const render = (value: unknown) => renderToStaticMarkup(<AbsenceChecks result={{ absence_assessments: value }}/>);
afterEach(() => setLocale("ko"));
describe("declared absence checks", () => {
  it("leaves old and unsupported versions without invented check records", () => {
    expect(render(undefined)).toBe("");
    expect(render({ ...payload, schema_version: "9.0.0" })).toBe("");
  });
  it("reads the new checker alongside historical records without rewriting either", () => {
    expect(render({ ...payload, checker_version: "literal-absence:1.1.0" })).toBe(render(payload));
    expect(render({ ...payload, checker_version: "literal-absence:9.0.0" })).toBe("");
  });
  it("separates input and outside witnesses, unsupported and scoped absence", () => {
    const html = render(payload);
    expect(html).toContain("AI가 받은 자료에 있는데 없다고 함");
    expect(html).toContain("AI에게 주지 않은 자료에 있음");
    expect(html).toContain("검사한 범위에서 못 찾음");
    expect(html).toContain("미검사");
    expect(html).toContain("추가 근거를 다음 AI 입력에 전달하지 못했습니다");
    expect(html.match(/role="alert"/g)).toHaveLength(2);
  });
  it("keeps English wording, exact targets and the original answer beside the hold", () => {
    setLocale("en");
    const answer = "The original answer remains unchanged.";
    const html = renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{ answer, answer_status: "PARTIAL_HOLD", absence_assessments: payload }} onDetail={() => {}}/>);
    expect(html).toContain(answer);
    expect(html).toContain("The model claimed absence of a string present in its input");
    expect(html).toContain("Found in allowed material outside the model input");
    expect(html).toContain("반복 분포");
    expect(html).not.toContain("AUDITED_COMPLETE");
  });
  it("renders full records and does not call an unchecked input absent", () => {
    const html = render({ ...payload, active_stage_ref: { revision_id: "new" }, items: [
      { claim: { target: "Old target" }, origin_stage_ref: { revision_id: "old" }, effective: "FOUND_IN_MODEL_INPUT" },
      { claim: { target: "Visible target" }, origin_stage_ref: { revision_id: "new" }, a_result: { outcome: "NOT_CHECKED" }, b_result: { outcome: "FOUND" }, effective: "FOUND_A_SCOPE_UNCHECKED", warning_code: "FOUND_A_SCOPE_UNCHECKED" },
    ] });
    expect(html).toContain("AI 입력 범위는 미확인");
    expect(html).toContain("Visible target");
    expect(html).not.toContain("Old target");
    expect(html).not.toContain("AI에게 주지 않은 자료에 있음");
  });
  it("does not treat an empty declaration as successful checking of all prose", () => {
    const html = render({ ...payload, items: [] });
    expect(html).toContain("부재 주장이 선언되지 않았습니다");
    expect(html).toContain("모든 문장을 검사했다는 뜻은 아닙니다");
  });
});
