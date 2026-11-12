/** Complete Simplified Chinese sentences for rule-derived display; source titles/conditions stay verbatim. */
export const criterionVerdictsZhHant: Record<string, string> = {
  PASS_COMPUTED: "標準已滿足",
  FAIL_COMPUTED: "標準未滿足",
  HOLD_NO_RESULT: "暫緩 · 沒有結果",
  HOLD_INVALID_RESULT: "暫緩 · 結果不可用",
  HOLD_NO_RULE: "暫緩 · 沒有評估規則",
};
export const requirementVerdictsZhHant: Record<string, string> = {
  PASS: "所有必要標準均已滿足",
  FAIL: "標準未滿足",
  FAIL_WITH_INCOMPLETE_COVERAGE: "標準未滿足 · 部分條件沒有結果",
  HOLD_INCOMPLETE: "暫緩 · 部分條件沒有結果",
  HOLD_NO_CRITERIA: "暫緩 · 沒有必要標準",
};
const reasons: Record<string, string> = {
  NO_RULE: "此標準沒有評估規則，因此沒有進行比較。",
  NO_REQUIRED_CRITERIA: "沒有定義任何必要標準，因此不算透過。",
  VALUE_MISSING: "結果沒有值，因此沒有進行比較。",
  VALUE_NOT_A_NUMBER: "結果不是數值，因此沒有進行比較。",
  VALUE_DISAGREES_WITH_COUNTS: "結果與按分子/分母計算的值不一致，因此沒有進行比較。",
  DENOMINATOR_NOT_POSITIVE: "分母為零或為負，因此沒有進行比較。",
  VALUE_OUT_OF_RANGE: "該值過大，無法計算。",
};

export function reasonTraditional(
  code: string,
  titleOf: (id: string) => string,
  condition?: string | null,
): string {
  const [head, ...rest] = code.split(":");
  const tail = rest.join(":");
  if (reasons[head]) return reasons[head];
  if (head === "NO_RESULT")
    return (condition ?? tail) ? `條件 ${condition ?? tail} 還沒有結果。` : "目前還沒有結果。";
  if (head === "THRESHOLD_NOT_MET") {
    const match = /value=(\S+) need (>=|<=) (\S+) (.*)$/.exec(tail);
    if (!match) return "測量值未滿足標準。";
    return `測量值 ${match[1]} 未滿足${match[2] === ">=" ? "不低於" : "不高於"} ${match[3]} 的要求${match[4] ? `（${match[4]}）` : ""}。`;
  }
  if (head === "UNIT_MISMATCH") {
    const match = /:([^!]*)!=(.*)$/.exec(tail);
    return match
      ? `結果的單位（${match[1]}）與標準的單位（${match[2]}）不同，因此沒有進行比較。`
      : "結果與標準的單位不同，因此沒有進行比較。";
  }
  if (rest.length && criterionVerdictsZhHant[tail])
    return `${titleOf(head)}：${criterionVerdictsZhHant[tail]}`;
  return "無法用文字描述這一原因。請檢視技術詳情。";
}

export function investigationQuestionZhHant(
  title: string,
  subject: "CRITERION" | "REQUIREMENT",
  verdict: string,
  reasons: string[],
  failed: boolean,
): string {
  const ask = failed
    ? "請找出可能的原因，以及能區分這些原因的測試。"
    : "請說明要做出評估還缺少什麼，並找出可能的原因，以及能區分這些原因的測試。";
  return [
    `${subject === "CRITERION" ? "標準" : "要求"}“${title}”的評估是“${verdict}”。`,
    ...reasons,
    ask,
  ].join("");
}
