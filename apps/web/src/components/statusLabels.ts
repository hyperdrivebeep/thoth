import { t } from "../i18n/locale";
import { statusText } from "../i18n/statusResources";
/** Plain-Korean labels for values the server records. Unknown values are shown as recorded. */











export function statusLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? statusText("labels", value) ?? null : null;
}


export function reversibilityLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? statusText("reversibility", value) ?? value : null;
}
export function actionStateLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? statusText("actionState", value) ?? statusLabel(value) ?? value : null;
}
/** Terms inside a criteria row (target, relation, validation, blocker). */

export function coverageTermLabel(value: string): string { return statusText("coverageTerms", value) ?? value; }

export function actionFamilyLabel(value: string): string | null { return statusText("actionFamilies", value) ?? null; }

export const memoryStateLabels = {
  transition: (value: string) => ({ get COMMIT() { return t("state.applied"); }, get REVISE() { return t("state.revise"); }, get HOLD() { return t("state.hold"); }, get QUARANTINE() { return t("state.quarantine"); } } as Record<string, string>)[value] ?? value,
  support: (value: string) => value === "SUPPORTED" ? t("state.supported") : t("state.supportNeeded"),
  authority: (value: string) => value === "AUTHORITATIVE" ? t("state.authoritative") : t("state.authorityNeeded"),
};
export function memoryExclusionLabel(value: string): string { return statusText("memoryExclusions", value) ?? value; }
const effortBands: Record<string, Record<string, string>> = {
  TIME: { get LOW() { return t("state.short"); }, get MEDIUM() { return t("state.mediumTime"); }, get HIGH() { return t("state.long"); }, get UNKNOWN() { return t("state.unknown"); } },
  COST_EFFORT: { get LOW() { return t("state.low"); }, get MEDIUM() { return t("state.medium"); }, get HIGH() { return t("state.high"); }, get UNKNOWN() { return t("state.unknown"); } },
};
export function effortDimensionLabel(value: string): string { return value === "TIME" ? t("state.time") : value === "COST_EFFORT" ? t("state.cost") : value; }
export function effortBandLabel(dimension: string, band: string): string { return effortBands[dimension]?.[band] ?? band; }
export function effortBandOptions(dimension: string): { value: string; label: string }[] {
  return ["LOW", "MEDIUM", "HIGH", "UNKNOWN"].map(value => ({ value, label: effortBandLabel(dimension, value) }));
}
export function estimatorLabel(value: string): string { return value === "AI" ? t("state.aiEstimate") : value === "HUMAN" ? t("state.humanEstimate") : value === "RULE" ? t("state.ruleEstimate") : value; }
/** Korean wording for the next step, chosen by action type; the server's own (English) label stays out of the visible text. */
export function nextActionLabel(action: { action_type: string; reason_codes?: string[] }): string {
  switch (action.action_type) {
    case "REVIEW_GAPS": return t("state.reviewGaps");
    case "REVIEW_CURRENTNESS": return t("state.reviewCurrentness");
    case "OPEN_RESULT_DETAIL": return action.reason_codes?.includes("RESULT_NOT_PRODUCED") ? t("state.checkAttempt") : t("state.checkResult");
    case "START_FOLLOWUP": return t("state.followup");
    case "NONE": return t("state.noNextAction");
    default: return t("state.unknownAction");
  }
}
export function counterReviewLabel(value: string): string { return statusText("counterReview", value) ?? value; }
export function answerStatusLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? statusText("answerStatus", value) ?? null : null;
}
export function providerLabel(value: string): string { return statusText("providers", value) ?? value; }
export function eyebrowLabel(value: string): string { return statusText("eyebrows", value) ?? value; }
