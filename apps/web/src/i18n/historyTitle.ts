import { getLocale } from "./locale";

/** Titles owned by HistoryProjection/restore profiles, never authored questions. */
const titlesEn: Record<string, Readonly<Record<string, string>>> = {
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
const titlesZh: Record<string, Readonly<Record<string, string>>> = {
  REVISION: {
    "결정 객체 변경": "决策对象已更新", "근거 충분성 평가 변경": "证据充分性评估已更新",
    "가설 변경": "假设已更新", "가설 구성 변경": "假设组合已更新",
    "행동 제안 변경": "行动建议已更新", "행동 구성 변경": "行动组合已更新",
    "행동 계획 변경": "行动计划已更新", "근거 연결 변경": "证据链接已更新",
    "기억 변경": "记忆已更新", "연구 항목 변경": "研究项目已更新",
  },
  MEMORY: { "연구 기억 기록": "已记录研究记忆" },
  RESTORE: { "연구 항목 복원": "研究项目已恢复" },
  RESULT: { "연구 진행 중간 저장": "研究检查点", "당시 연구 답변": "已保存的研究回答" },
};
const titles = { en: titlesEn, "zh-Hans": titlesZh } as const;

export function historyTitle(item: { kind: string; title: string }): string {
  const locale = getLocale();
  return locale === "ko" ? item.title : titles[locale][item.kind]?.[item.title] ?? item.title;
}
