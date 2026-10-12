import { t, getLocale } from "../../i18n/locale";
import type { Currentness } from "../../api/historyModels";

const states: Record<string, string> = {
  get CURRENT() { return t("history.current"); }, get REVIEW_REQUIRED() { return t("history.review"); }, get RECALCULATION_REQUIRED() { return t("history.review"); },
  get STALE() { return t("history.review"); }, get INVALIDATED() { return t("history.invalid"); }, get UNKNOWN_BASIS() { return t("history.checkBasis"); },
  get UNKNOWN() { return t("history.unknownBasis"); }, get UNAVAILABLE() { return t("history.unavailable"); }, get UNASSESSED() { return t("history.unassessed"); },
};
export function currentnessLabel(currentness: Currentness): string {
  return states[currentness.state] ?? t("history.unknownBasis");
}
export function currentnessReasonNote(currentness: Currentness): string | null {
  return currentness.reasons.includes("MEMORY_CORRECTED_AFTER_RESULT") ? t("history.memoryCorrected") : null;
}
export function currentnessTone(currentness: Currentness): "success" | "warning" | "none" {
  if (currentness.state === "CURRENT") return "success";
  return ["REVIEW_REQUIRED", "RECALCULATION_REQUIRED", "STALE", "INVALIDATED"].includes(currentness.state) ? "warning" : "none";
}
const kinds: Record<string, string> = { get REQUEST() { return t("history.question"); }, get RESULT() { return t("history.answer"); }, get REVISION() { return t("history.change"); }, get RESTORE() { return t("history.restore"); }, get MEMORY() { return t("result.memory"); } };
export const kindLabel = (kind: string) => kinds[kind] ?? t("history.record");
export function formatHistoryTime(value: string | null): string {
  if (!value) return t("history.noTime");
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? t("history.unknownTime") : date.toLocaleString(getLocale() === "en" ? "en-US" : "ko-KR", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export const groupLabels: Record<string, string> = {
  get CONTENT() { return t("trace.field.content"); }, get CONCLUSION() { return t("history.assessment"); }, get EVIDENCE() { return t("history.evidence"); }, get CONDITION() { return t("history.condition"); }, get STATUS() { return t("history.assessmentState"); },
  get IMPACT() { return t("history.impact"); }, get ACTION() { return t("history.action"); }, get MEMORY() { return t("result.memory"); }, get OTHER() { return t("history.other"); },
};
export function describeValue(value: unknown, missing: boolean): string {
  if (missing) return t("history.itemAbsent");
  if (value === null) return t("history.noValue");
  if (typeof value === "string") return value || t("history.empty");
  return JSON.stringify(value, null, 2) ?? t("history.valueUnknown");
}
