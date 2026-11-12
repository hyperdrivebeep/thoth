/** Complete Japanese sentences for rule-derived display; source titles/conditions stay verbatim. */
export const criterionVerdictsJa: Record<string, string> = {
  PASS_COMPUTED: "基準を満たす",
  FAIL_COMPUTED: "基準を満たさない",
  HOLD_NO_RESULT: "保留 · 結果なし",
  HOLD_INVALID_RESULT: "保留 · 結果を使えない",
  HOLD_NO_RULE: "保留 · 判定ルールなし",
};
export const requirementVerdictsJa: Record<string, string> = {
  PASS: "必須の基準をすべて満たす",
  FAIL: "基準を満たさない",
  FAIL_WITH_INCOMPLETE_COVERAGE: "基準を満たさない · 結果のない条件もある",
  HOLD_INCOMPLETE: "保留 · 結果のない条件がある",
  HOLD_NO_CRITERIA: "保留 · 必須の基準がない",
};
const reasons: Record<string, string> = {
  NO_RULE: "この基準には判定ルールがないため、比較しませんでした。",
  NO_REQUIRED_CRITERIA: "必須の基準が定義されていないため、合格とは見なしません。",
  VALUE_MISSING: "結果に値がないため、比較しませんでした。",
  VALUE_NOT_A_NUMBER: "結果が数値ではないため、比較しませんでした。",
  VALUE_DISAGREES_WITH_COUNTS: "結果が分子・分母から計算した値と異なるため、比較しませんでした。",
  DENOMINATOR_NOT_POSITIVE: "分母がゼロまたは負のため、比較しませんでした。",
  VALUE_OUT_OF_RANGE: "値が大きすぎて計算できませんでした。",
};

export function reasonJapanese(
  code: string,
  titleOf: (id: string) => string,
  condition?: string | null,
): string {
  const [head, ...rest] = code.split(":");
  const tail = rest.join(":");
  if (reasons[head]) return reasons[head];
  if (head === "NO_RESULT")
    return (condition ?? tail)
      ? `条件 ${condition ?? tail} の結果がまだありません。`
      : "結果がまだありません。";
  if (head === "THRESHOLD_NOT_MET") {
    const match = /value=(\S+) need (>=|<=) (\S+) (.*)$/.exec(tail);
    if (!match) return "測定値が基準を満たしていません。";
    return `測定値 ${match[1]} は、${match[3]} ${match[2] === ">=" ? "以上" : "以下"}という要件を満たしていません${match[4] ? `（${match[4]}）` : ""}。`;
  }
  if (head === "UNIT_MISMATCH") {
    const match = /:([^!]*)!=(.*)$/.exec(tail);
    return match
      ? `結果の単位（${match[1]}）が基準の単位（${match[2]}）と異なるため、比較しませんでした。`
      : "結果と基準の単位が異なるため、比較しませんでした。";
  }
  if (rest.length && criterionVerdictsJa[tail])
    return `${titleOf(head)}：${criterionVerdictsJa[tail]}`;
  return "この理由を文章にできませんでした。技術的な詳細を確認してください。";
}

export function investigationQuestionJa(
  title: string,
  subject: "CRITERION" | "REQUIREMENT",
  verdict: string,
  reasons: string[],
  failed: boolean,
): string {
  const ask = failed
    ? "考えられる原因と、それらを見分けるテストを探してください。"
    : "判定するのに何がさらに必要か、考えられる原因と、それらを見分けるテストを探してください。";
  return [
    `${subject === "CRITERION" ? "基準" : "要件"}「${title}」の判定は「${verdict}」です。`,
    ...reasons,
    ask,
  ].join("");
}
