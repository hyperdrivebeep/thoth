import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { applyFailureText, changeSentence, describeCondition, describeConditionText, issueText, lossText, NEXT_STEP_DECIDERS, nextStep, reasonSentence, verdictLook } from "./traceText";

const source = (path: string) => readFileSync(new URL("../../../../src/thoth/" + path, import.meta.url), "utf8");
const RAW_CODE = /[A-Z]{3,}_[A-Z_]{3,}/;

describe("trace wording stays in step with the backend", () => {
  it("has plain words for every problem code the import can report", () => {
    const text = source("application/services/trace_csv.py") + source("application/services/verification_trace_import.py");
    const codes = [...new Set([...text.matchAll(/Issue\(\s*"([A-Z_]+)"/g)].map(match => match[1]))];
    expect(codes.length).toBeGreaterThan(20);
    for (const code of codes) {
      const said = issueText({ code, row: 3, ref: "X-1", detail: "detail" });
      expect(said.message, code).not.toContain("이 파일을 반영할 수 없는 문제가 있습니다");
      expect(said.message + said.next, code).not.toMatch(RAW_CODE);
      expect(said.next.length, code).toBeGreaterThan(5);
    }
  });

  it("has a look for every verdict state, and the wording never shows the state constant", () => {
    const text = source("domain/verification_trace.py");
    const states = (name: string) => {
      const body = text.split("class " + name)[1].split("\nclass ")[0];
      return [...body.matchAll(/^\s+([A-Z_]+) = "([A-Z_]+)"$/gm)].map(match => match[2]);
    };
    const criterion = states("CriterionVerdictState(StrEnum)");
    const requirement = states("RequirementVerdictState(StrEnum)");
    expect(criterion).toHaveLength(5); expect(requirement).toHaveLength(5);
    for (const [kind, list] of [["CRITERION", criterion], ["REQUIREMENT", requirement]] as const) {
      for (const state of list) {
        const look = verdictLook(kind, state);
        expect(look.text, state).not.toBe("알 수 없는 판정");
        expect(look.text, state).not.toMatch(RAW_CODE);
        expect(look.intent === "none", state).toBe(false);
      }
    }
  });

  it("turns every reason the rule can give into a sentence", () => {
    const names = (id: string) => (id === "C-1" ? "탐지율(맑음)" : id);
    const codes = ["NO_RULE", "NO_RESULT:weather=rain", "THRESHOLD_NOT_MET:R-1:value=0.80 need >= 0.90 ratio", "UNIT_MISMATCH:R-1:per_hour!=per_min",
      "VALUE_MISSING:R-1", "VALUE_NOT_A_NUMBER:R-1", "VALUE_DISAGREES_WITH_COUNTS:R-1", "DENOMINATOR_NOT_POSITIVE:R-1", "VALUE_OUT_OF_RANGE:R-1",
      "NO_REQUIRED_CRITERIA", "C-1:HOLD_NO_RESULT"];
    for (const code of codes) {
      const sentence = reasonSentence(code, names);
      expect(sentence, code).not.toContain("이유를 문장으로 바꾸지 못했습니다");
      expect(sentence, code).not.toMatch(RAW_CODE);
    }
    expect(reasonSentence("THRESHOLD_NOT_MET:R-1:value=0.80 need >= 0.90 ratio", names)).toBe("측정값 0.80이 기준 0.90 이상(ratio)에 못 미칩니다.");
    expect(reasonSentence("THRESHOLD_NOT_MET:R-2:value=0.70 need <= 0.50 per_min", names)).toBe("측정값 0.70이 기준 0.50 이하(per_min)를 넘습니다.");
    expect(reasonSentence("C-1:HOLD_NO_RESULT", names)).toBe("탐지율(맑음): 보류 · 결과 없음");
  });

  it("says what a refused apply means and what to do, and what the file cannot carry", () => {
    for (const code of ["TRACE_IMPORT_PREVIEW_STALE", "TRACE_IMPORT_INPUT_CHANGED", "TRACE_IMPORT_NOT_APPLICABLE:STALE_BASE", "TRACE_REVISION_CONFLICT", "closing or archived project"]) {
      const said = applyFailureText(new Error(code));
      expect(said.message + said.next).not.toMatch(RAW_CODE);
      expect(said.message).not.toBe("반영하지 못했습니다. 아무것도 저장되지 않았습니다.");
    }
    for (const line of source("application/services/trace_csv.py").match(/"(\w+): [^"]+"/g) ?? []) {
      if (!/^"(evidence_text|verdict_history|confirmations|selection_policy|pending_changes|closures):/.test(line)) continue;
      expect(lossText(line.slice(1, -1))).not.toMatch(RAW_CODE);
    }
    expect(lossText("closures: closure_status is for reading only; editing it changes nothing. Record closures in THOTH.")).toContain("처분 표시 열");
  });
});

describe("conditions in plain words", () => {
  it("names the known weather conditions and shows anything else as written", () => {
    expect(describeCondition("weather", "rain")).toBe("날씨: 비");
    expect(describeCondition("weather", "dry")).toBe("날씨: 맑음(건조)");
    expect(describeCondition("weather", "fog")).toBe("날씨: 안개");
    expect(describeCondition("weather", "snow")).toBe("날씨: snow");
    expect(describeCondition("temperature", "hot")).toBe("temperature = hot");
    expect(describeConditionText("weather=rain")).toBe("날씨: 비");
    expect(describeConditionText("no key value")).toBe("no key value");
  });

  it("builds the no-result sentence from the verdict's condition, not from the code text", () => {
    const names = (id: string) => id;
    expect(reasonSentence("NO_RESULT:weather=rain", names, "weather=fog")).toBe("날씨: 안개 조건의 결과가 아직 없습니다.");
    expect(reasonSentence("NO_RESULT:weather=rain", names)).toBe("날씨: 비 조건의 결과가 아직 없습니다.");
  });

  it("describes an added result from the stored result, and a changed condition in words", () => {
    const result = { result_id: "R-1", result_revision: 1, criterion_id: "C", condition: "weather=rain", value: "0.80", raw_value: null, unit: "ratio", numerator: 16, denominator: 20, observed_at: "2026-01-02T00:00:00Z", source_span_refs: [] };
    const added = { kind: "RESULT", ref_id: "R-1", field: "*", before: null, after: "0.80 ratio @weather=rain", before_revision: null, after_revision: 1, criterion_ids: [], requirement_ids: [] };
    expect(changeSentence(added, { results: [result], rules: [] })).toBe("결과 추가: R-1 (0.80 ratio (16/20) · 날씨: 비)");
    expect(changeSentence(added)).toBe("결과 추가: R-1");
    const changed = { ...added, field: "condition", before: "weather=dry", after: "weather=rain" };
    expect(changeSentence(changed)).toBe("결과 R-1의 조건: 날씨: 맑음(건조) → 날씨: 비");
  });
});

describe("the next check for a held or failed line", () => {
  const BANNED = /재시험 필수|원인은|기준을 낮추|통과 가능|승인됨|종결|해결|면제|AI 추천/;
  const LEAK = /HOLD_|FAIL_|PASS_|[a-z]+_[a-z]+/;
  const cases: [("CRITERION" | "REQUIREMENT"), string, number, string | null][] = [
    ["CRITERION", "HOLD_NO_RESULT", 1, NEXT_STEP_DECIDERS.test],
    ["CRITERION", "HOLD_INVALID_RESULT", 1, NEXT_STEP_DECIDERS.validity],
    ["CRITERION", "HOLD_NO_RULE", 1, NEXT_STEP_DECIDERS.rule],
    ["CRITERION", "FAIL_COMPUTED", 1, NEXT_STEP_DECIDERS.disposition],
    ["REQUIREMENT", "FAIL", 1, NEXT_STEP_DECIDERS.disposition],
    ["REQUIREMENT", "FAIL_WITH_INCOMPLETE_COVERAGE", 2, NEXT_STEP_DECIDERS.disposition],
    ["REQUIREMENT", "HOLD_INCOMPLETE", 1, null],
    ["REQUIREMENT", "HOLD_NO_CRITERIA", 1, NEXT_STEP_DECIDERS.rule],
  ];

  it.each(cases)("gives %s %s its own check lines and decider", (kind, state, lines, decider) => {
    const step = nextStep(kind, state, false)!;
    expect(step.lines).toHaveLength(lines);
    expect(step.lines[0].decider).toBe(decider);
  });

  it("tells a failed requirement with missing conditions both to investigate the failure and to plan the untested conditions", () => {
    const step = nextStep("REQUIREMENT", "FAIL_WITH_INCOMPLETE_COVERAGE", false)!;
    expect(step.lines[0].text).toContain("원인 후보");
    expect(step.lines[1].text).toContain("시험하지 않은 조건");
    expect(step.lines[1].text).toContain("시험 계획");
    // each line names its own decider: the authority for the failure, the test owner for the plan
    expect(step.lines.map(item => item.decider)).toEqual([NEXT_STEP_DECIDERS.disposition, NEXT_STEP_DECIDERS.test]);
  });

  it("gives a met line nothing, unless its basis changed", () => {
    for (const [kind, state] of [["CRITERION", "PASS_COMPUTED"], ["REQUIREMENT", "PASS"]] as const) {
      expect(nextStep(kind, state, false)).toBeNull();
      const stale = nextStep(kind, state, true)!;
      expect(stale.lines).toHaveLength(1);
      expect(stale.lines[0].text).toContain("이전 안내는 이전 근거 기준입니다");
      expect(stale.lines[0].decider).toBeNull();
    }
  });

  it("puts the changed-basis line first on a held line and keeps the state's own lines", () => {
    const step = nextStep("CRITERION", "HOLD_NO_RESULT", true)!;
    expect(step.lines[0].text).toContain("이전 안내는 이전 근거 기준입니다");
    expect(step.lines).toHaveLength(2);
    expect(step.lines[1].decider).toBe(NEXT_STEP_DECIDERS.test);
  });

  it.each([
    ["HOLD_NO_RESULT", ["UNTESTED"], "연결된 시험의 결과가 아직 들어오지 않았습니다", NEXT_STEP_DECIDERS.test],
    ["HOLD_NO_RESULT", ["UNLINKED"], "확인할 시험이 연결돼 있지 않습니다", NEXT_STEP_DECIDERS.rule],
    ["HOLD_NO_RESULT", ["CONDITION_MISMATCH"], "기준의 조건과 달라 쓰지 않았습니다", NEXT_STEP_DECIDERS.test],
    ["HOLD_INVALID_RESULT", ["UNIT"], "단위가 기준의 단위와 다릅니다", NEXT_STEP_DECIDERS.validity],
    ["HOLD_INVALID_RESULT", ["DENOMINATOR"], "분모가 0 이하", NEXT_STEP_DECIDERS.validity],
    ["HOLD_INVALID_RESULT", ["VALUE"], "값이 비었거나", NEXT_STEP_DECIDERS.validity],
  ] as const)("gives %s with tag %j its own check and decider", (state, tags, phrase, decider) => {
    const step = nextStep("CRITERION", state, false, tags)!;
    expect(step.lines).toHaveLength(1);
    expect(step.lines[0].text).toContain(phrase);
    expect(step.lines[0].decider).toBe(decider);
  });

  it("gives one line per tag in a fixed order, and the general check when no tag has its own", () => {
    const both = nextStep("CRITERION", "HOLD_INVALID_RESULT", false, ["VALUE", "UNIT"])!;
    expect(both.lines).toHaveLength(2);
    expect(both.lines[0].text).toContain("단위"); expect(both.lines[1].text).toContain("값이 비었거나");
    const plain = nextStep("CRITERION", "HOLD_INVALID_RESULT", false, ["BASIS_CHANGED"])!;
    expect(plain.lines).toEqual(nextStep("CRITERION", "HOLD_INVALID_RESULT", false)!.lines);
    expect(nextStep("CRITERION", "HOLD_NO_RESULT", false, ["UNIT"])!.lines).toEqual(nextStep("CRITERION", "HOLD_NO_RESULT", false)!.lines); // a tag from the other state is ignored
    expect(nextStep("REQUIREMENT", "HOLD_INCOMPLETE", false, ["UNTESTED"])!.lines).toHaveLength(1); // a requirement never takes a criterion's tag
  });

  it("guesses nothing for a state it does not know, or for the other kind's state", () => {
    expect(nextStep("CRITERION", "SOMETHING_NEW", false)).toBeNull();
    expect(nextStep("CRITERION", "SOMETHING_NEW", true)).toBeNull();
    expect(nextStep("CRITERION", "HOLD_INCOMPLETE", false)).toBeNull();
    expect(nextStep("REQUIREMENT", "FAIL_COMPUTED", false)).toBeNull();
  });

  it("never uses a forbidden phrase, a state constant or a field name in any line, or in the decider names", () => {
    const lines: string[] = [];
    for (const [kind, state] of cases) for (const stale of [false, true]) { const step = nextStep(kind, state, stale)!; lines.push(...step.lines.map(item => item.text)); }
    for (const [state, tags] of [["HOLD_NO_RESULT", ["UNTESTED", "UNLINKED", "CONDITION_MISMATCH"]], ["HOLD_INVALID_RESULT", ["UNIT", "DENOMINATOR", "VALUE"]]] as const) {
      for (const tag of tags) lines.push(...nextStep("CRITERION", state, true, [tag])!.lines.map(item => item.text));
    }
    lines.push(...nextStep("CRITERION", "PASS_COMPUTED", true)!.lines.map(item => item.text), ...Object.values(NEXT_STEP_DECIDERS));
    expect(lines.length).toBeGreaterThan(10);
    for (const line of lines) { expect(line).not.toMatch(BANNED); expect(line).not.toMatch(LEAK); }
  });

  it("covers every verdict state the backend can give, either with a check or by design with none", () => {
    const text = source("domain/verification_trace.py");
    const states = (name: string) => [...text.split("class " + name)[1].split("\nclass ")[0].matchAll(/^\s+([A-Z_]+) = "([A-Z_]+)"$/gm)].map(match => match[2]);
    for (const [kind, list] of [["CRITERION", states("CriterionVerdictState(StrEnum)")], ["REQUIREMENT", states("RequirementVerdictState(StrEnum)")]] as const) {
      for (const state of list) {
        const met = state === "PASS" || state === "PASS_COMPUTED";
        expect(nextStep(kind, state, false) === null, kind + " " + state).toBe(met);
      }
    }
  });
});

describe("particles after a value or an id follow how it is spoken", () => {
  const names = (id: string) => id;
  const sentence = (value: string) => reasonSentence(`THRESHOLD_NOT_MET:R-1:value=${value} need >= 0.90 ratio`, names);

  it("picks 이 or 가 after the measured value", () => {
    expect(sentence("0.80")).toBe("측정값 0.80이 기준 0.90 이상(ratio)에 못 미칩니다.");
    expect(sentence("0.95")).toBe("측정값 0.95가 기준 0.90 이상(ratio)에 못 미칩니다.");
    expect(sentence("16/20")).toBe("측정값 16/20이 기준 0.90 이상(ratio)에 못 미칩니다.");
    expect(reasonSentence("THRESHOLD_NOT_MET:R-2:value=0.70 need <= 0.50 per_min", names)).toBe("측정값 0.70이 기준 0.50 이하(per_min)를 넘습니다.");
    expect(sentence("0.72")).toContain("0.72가 기준");
    expect(sentence("0.45")).toContain("0.45가 기준");
    // never both forms together for a number
    for (const value of ["0.80", "0.95", "16/20", "0.70", "0.9", "1"]) expect(sentence(value)).not.toContain("이(가)");
  });

  it("picks the particle after the id in an import problem", () => {
    const said = (code: string, ref: string | null) => issueText({ code, row: 4, ref, detail: "" }).message;
    expect(said("DELETE_TARGET_NOT_FOUND", "SYN-C-DET-DRY")).toContain("지우려는 것 SYN-C-DET-DRY가 추적표에 없습니다");
    expect(said("DELETE_TARGET_NOT_FOUND", "SYN-C-DET-RAIN")).toContain("지우려는 것 SYN-C-DET-RAIN이 추적표에 없습니다");
    expect(said("DELETE_AND_UPSERT_SAME_TARGET", "SYN-C-DET-DRY")).toContain("SYN-C-DET-DRY를 같은 파일에서 고치면서");
    expect(said("DELETE_AND_UPSERT_SAME_TARGET", "SYN-C-FA-RAIN")).toContain("SYN-C-FA-RAIN을 같은 파일에서 고치면서");
    expect(said("DUPLICATE_ID_IN_FILE", "SYN-C-DET-DRY")).toContain("같은 ID SYN-C-DET-DRY가 파일에 두 번");
    expect(said("DUPLICATE_ID_IN_FILE", "SYN-C-DET-RAIN")).toContain("같은 ID SYN-C-DET-RAIN이 파일에 두 번");
    expect(said("EXCEL_ID_SUSPECTED", "SYN-C-DET-DRY")).toContain("ID SYN-C-DET-DRY는 스프레드시트가");
    expect(said("EXCEL_ID_SUSPECTED", "SYN-C-DET-RAIN")).toContain("ID SYN-C-DET-RAIN은 스프레드시트가");
  });
});
