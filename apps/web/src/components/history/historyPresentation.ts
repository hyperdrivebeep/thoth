import type { Currentness } from "../../api/historyModels";

const states: Record<string, string> = {
  CURRENT: "현재 기준과 일치", REVIEW_REQUIRED: "재검토 필요", RECALCULATION_REQUIRED: "재검토 필요",
  STALE: "재검토 필요", INVALIDATED: "현재 적용 불가", UNKNOWN_BASIS: "현재 기준 확인 필요",
  UNKNOWN: "현재 기준 미확인", UNAVAILABLE: "현재 읽을 수 없음", UNASSESSED: "아직 평가하지 않음",
};
export function currentnessLabel(currentness: Currentness): string {
  return states[currentness.state] ?? "현재 기준 미확인";
}
export function currentnessTone(currentness: Currentness): "success" | "warning" | "none" {
  if (currentness.state === "CURRENT") return "success";
  return ["REVIEW_REQUIRED", "RECALCULATION_REQUIRED", "STALE", "INVALIDATED"].includes(currentness.state) ? "warning" : "none";
}
const kinds: Record<string, string> = { REQUEST: "질문", RESULT: "답변", REVISION: "변경", RESTORE: "복원", MEMORY: "프로젝트 기억" };
export const kindLabel = (kind: string) => kinds[kind] ?? "연구 기록";
export function formatHistoryTime(value: string | null): string {
  if (!value) return "시각 미기록";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "시각 미확인" : date.toLocaleString("ko-KR", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export const groupLabels: Record<string, string> = {
  CONTENT: "내용", CONCLUSION: "판단", EVIDENCE: "근거", CONDITION: "조건", STATUS: "판단 상태",
  IMPACT: "영향", ACTION: "행동", MEMORY: "프로젝트 기억", OTHER: "기타 변경",
};
export function describeValue(value: unknown, missing: boolean): string {
  if (missing) return "항목 없음";
  if (value === null) return "값 미기록";
  if (typeof value === "string") return value || "빈 내용";
  return JSON.stringify(value, null, 2) ?? "값 미확인";
}
