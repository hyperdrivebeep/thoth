import { bilingualRecord } from "./bilingualRecord";
const korean = {
  UNTESTED: { label: "미시험", hint: "이 기준에 연결된 시험은 있지만 아직 결과가 없습니다." },
  UNLINKED: { label: "미연결", hint: "이 기준에 연결된 시험이 없어 결과가 들어올 곳이 없습니다." },
  UNIT: { label: "단위 오류", hint: "결과의 단위가 기준의 단위와 달라 비교하지 않았습니다." },
  DENOMINATOR: { label: "분모 오류", hint: "결과의 분모가 0 이하이거나, 값이 분자·분모로 계산한 값과 달라 비교하지 않았습니다." },
  VALUE: { label: "값 오류", hint: "결과의 값이 비었거나 숫자가 아니거나 너무 커서 비교하지 않았습니다." },
  CONDITION_MISMATCH: { label: "조건 불일치", hint: "결과는 있지만 기준의 조건과 달라 쓰지 않았습니다." },
  NO_RULE: { label: "규칙 없음", hint: "이 기준에는 판정 규칙이 없어 비교하지 않았습니다." },
  VALID_MISS: { label: "유효한 미달", hint: "결과를 비교할 수 있었고 기준에 못 미쳤습니다." },
  REQUIRED_INCOMPLETE: { label: "필수 기준 미완료", hint: "필수 기준이 없거나, 필수 기준 중 아직 충족이나 미달이 정해지지 않은 것이 있습니다." },
  BASIS_CHANGED: { label: "근거 변경", hint: "이 판정이 기댄 규칙·결과·연결이 바뀌어 다시 계산해야 합니다." },
};
const english: Record<keyof typeof korean, {label: string; hint: string}> = {
  "UNTESTED": {
    "label": "Not tested",
    "hint": "A test is linked to this criterion, but it has no result yet."
  },
  "UNLINKED": {
    "label": "Not linked",
    "hint": "No test is linked to this criterion to receive a result."
  },
  "UNIT": {
    "label": "Unit error",
    "hint": "No comparison was made because the result and criterion units differ."
  },
  "DENOMINATOR": {
    "label": "Denominator error",
    "hint": "No comparison was made because the denominator is nonpositive or the value differs from the numerator/denominator calculation."
  },
  "VALUE": {
    "label": "Value error",
    "hint": "No comparison was made because the value is missing, nonnumeric, or too large."
  },
  "CONDITION_MISMATCH": {
    "label": "Condition mismatch",
    "hint": "A result exists but was not used because its conditions differ."
  },
  "NO_RULE": {
    "label": "No rule",
    "hint": "No comparison was made because this criterion has no assessment rule."
  },
  "VALID_MISS": {
    "label": "Valid result below criterion",
    "hint": "The result was comparable and did not meet the criterion."
  },
  "REQUIRED_INCOMPLETE": {
    "label": "Required criteria incomplete",
    "hint": "Required criteria are absent, or some have not yet been determined as met or unmet."
  },
  "BASIS_CHANGED": {
    "label": "Basis changed",
    "hint": "Rules, results, or links used by this assessment changed; recomputation is needed."
  }
};
export const traceReasonText = bilingualRecord(korean, english);
