/** Complete Simplified Chinese sentences for rule-derived display; source titles/conditions stay verbatim. */
export const criterionVerdictsZh: Record<string, string> = {
  PASS_COMPUTED: "标准已满足", FAIL_COMPUTED: "标准未满足",
  HOLD_NO_RESULT: "暂缓 · 没有结果", HOLD_INVALID_RESULT: "暂缓 · 结果不可用",
  HOLD_NO_RULE: "暂缓 · 没有评估规则",
};
export const requirementVerdictsZh: Record<string, string> = {
  PASS: "所有必要标准均已满足", FAIL: "标准未满足",
  FAIL_WITH_INCOMPLETE_COVERAGE: "标准未满足 · 部分条件没有结果",
  HOLD_INCOMPLETE: "暂缓 · 部分条件没有结果", HOLD_NO_CRITERIA: "暂缓 · 没有必要标准",
};
const reasons: Record<string, string> = {
  NO_RULE: "此标准没有评估规则，因此没有进行比较。",
  NO_REQUIRED_CRITERIA: "没有定义任何必要标准，因此不算通过。",
  VALUE_MISSING: "结果没有值，因此没有进行比较。",
  VALUE_NOT_A_NUMBER: "结果不是数值，因此没有进行比较。",
  VALUE_DISAGREES_WITH_COUNTS: "结果与按分子/分母计算的值不一致，因此没有进行比较。",
  DENOMINATOR_NOT_POSITIVE: "分母为零或为负，因此没有进行比较。",
  VALUE_OUT_OF_RANGE: "该值过大，无法计算。",
};

export function reasonChinese(code: string, titleOf: (id: string) => string, condition?: string | null): string {
  const [head, ...rest] = code.split(":");
  const tail = rest.join(":");
  if (reasons[head]) return reasons[head];
  if (head === "NO_RESULT") return (condition ?? tail) ? `条件 ${condition ?? tail} 还没有结果。` : "目前还没有结果。";
  if (head === "THRESHOLD_NOT_MET") {
    const match = /value=(\S+) need (>=|<=) (\S+) (.*)$/.exec(tail);
    if (!match) return "测量值未满足标准。";
    return `测量值 ${match[1]} 未满足${match[2] === ">=" ? "不低于" : "不高于"} ${match[3]} 的要求${match[4] ? `（${match[4]}）` : ""}。`;
  }
  if (head === "UNIT_MISMATCH") {
    const match = /:([^!]*)!=(.*)$/.exec(tail);
    return match ? `结果的单位（${match[1]}）与标准的单位（${match[2]}）不同，因此没有进行比较。`
      : "结果与标准的单位不同，因此没有进行比较。";
  }
  if (rest.length && criterionVerdictsZh[tail]) return `${titleOf(head)}：${criterionVerdictsZh[tail]}`;
  return "无法用文字描述这一原因。请查看技术详情。";
}

export function investigationQuestionZh(title: string, subject: "CRITERION" | "REQUIREMENT", verdict: string, reasons: string[], failed: boolean): string {
  const ask = failed ? "请找出可能的原因，以及能区分这些原因的测试。"
    : "请说明要做出评估还缺少什么，并找出可能的原因，以及能区分这些原因的测试。";
  return [`${subject === "CRITERION" ? "标准" : "要求"}“${title}”的评估是“${verdict}”。`, ...reasons, ask].join("");
}
