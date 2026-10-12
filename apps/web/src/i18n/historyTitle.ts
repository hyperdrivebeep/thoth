import { getLocale } from "./locale";

/** Titles owned by HistoryProjection/restore profiles, never authored questions. */
const titles: Record<string, Readonly<Record<string, string>>> = {
  REVISION: {
    "결정 객체 변경": "Decision updated", "근거 충분성 평가 변경": "Evidence assessment updated",
    "가설 변경": "Hypothesis updated", "가설 구성 변경": "Hypothesis portfolio updated",
    "행동 제안 변경": "Action proposal updated", "행동 구성 변경": "Action portfolio updated",
    "행동 계획 변경": "Action plan updated", "근거 연결 변경": "Evidence links updated",
    "기억 변경": "Memory updated", "연구 항목 변경": "Research item updated",
  },
  MEMORY: { "연구 기억 기록": "Research memory recorded" },
  RESTORE: { "연구 항목 복원": "Research item restored" },
  RESULT: { "연구 진행 중간 저장": "Research checkpoint", "당시 연구 답변": "Saved research answer" },
};

export function historyTitle(item: { kind: string; title: string }): string {
  return getLocale() === "en" ? titles[item.kind]?.[item.title] ?? item.title : item.title;
}
