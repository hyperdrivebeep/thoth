/** Complete English sentences for rule-derived display; source titles/conditions stay verbatim. */
export const criterionVerdicts: Record<string, string> = {
  PASS_COMPUTED: "Criterion met", FAIL_COMPUTED: "Criterion not met",
  HOLD_NO_RESULT: "On hold · no result", HOLD_INVALID_RESULT: "On hold · result unusable",
  HOLD_NO_RULE: "On hold · no assessment rule",
};
export const requirementVerdicts: Record<string, string> = {
  PASS: "All required criteria met", FAIL: "Criteria not met",
  FAIL_WITH_INCOMPLETE_COVERAGE: "Criteria not met · some conditions have no result",
  HOLD_INCOMPLETE: "On hold · some conditions have no result", HOLD_NO_CRITERIA: "On hold · no required criteria",
};
const reasons: Record<string, string> = {
  NO_RULE: "No comparison was made because this criterion has no assessment rule.",
  NO_REQUIRED_CRITERIA: "This is not a pass because no required criteria are defined.",
  VALUE_MISSING: "No comparison was made because the result has no value.",
  VALUE_NOT_A_NUMBER: "No comparison was made because the result is not numeric.",
  VALUE_DISAGREES_WITH_COUNTS: "No comparison was made because the result differs from the numerator/denominator calculation.",
  DENOMINATOR_NOT_POSITIVE: "No comparison was made because the denominator is zero or negative.",
  VALUE_OUT_OF_RANGE: "The value is too large to calculate.",
};

export function reasonEnglish(code: string, titleOf: (id: string) => string, condition?: string | null): string {
  const [head, ...rest] = code.split(":");
  const tail = rest.join(":");
  if (reasons[head]) return reasons[head];
  if (head === "NO_RESULT") return (condition ?? tail) ? `No result is available for condition ${condition ?? tail}.` : "No result is available yet.";
  if (head === "THRESHOLD_NOT_MET") {
    const match = /value=(\S+) need (>=|<=) (\S+) (.*)$/.exec(tail);
    if (!match) return "The measured value does not meet the criterion.";
    return `The measured value ${match[1]} does not meet the requirement of ${match[3]} or ${match[2] === ">=" ? "higher" : "lower"}${match[4] ? ` (${match[4]})` : ""}.`;
  }
  if (head === "UNIT_MISMATCH") {
    const match = /:([^!]*)!=(.*)$/.exec(tail);
    return match ? `No comparison was made because the result unit (${match[1]}) differs from the criterion unit (${match[2]}).`
      : "No comparison was made because the result and criterion units differ.";
  }
  if (rest.length && criterionVerdicts[tail]) return `${titleOf(head)}: ${criterionVerdicts[tail]}`;
  return "This reason could not be described. Check the technical details.";
}

export function investigationQuestion(title: string, subject: "CRITERION" | "REQUIREMENT", verdict: string, reasons: string[], failed: boolean): string {
  const ask = failed ? "Find possible causes and tests that distinguish them."
    : "Explain what else is needed for an assessment, and find possible causes and tests that distinguish them.";
  return [`The assessment for ${subject === "CRITERION" ? "criterion" : "requirement"} “${title}” is “${verdict}”.`, ...reasons, ask].join(" ");
}
