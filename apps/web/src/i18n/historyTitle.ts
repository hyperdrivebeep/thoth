import { getLocale } from "./locale";

/** Titles owned by HistoryProjection/restore profiles, never authored questions. */
const titlesEn: Record<string, Readonly<Record<string, string>>> = {
  REVISION: {
    "결정 객체 변경": "Decision updated",
    "근거 충분성 평가 변경": "Evidence assessment updated",
    "가설 변경": "Hypothesis updated",
    "가설 구성 변경": "Hypothesis portfolio updated",
    "행동 제안 변경": "Action proposal updated",
    "행동 구성 변경": "Action portfolio updated",
    "행동 계획 변경": "Action plan updated",
    "근거 연결 변경": "Evidence links updated",
    "기억 변경": "Memory updated",
    "연구 항목 변경": "Research item updated",
  },
  MEMORY: { "연구 기억 기록": "Research memory recorded" },
  RESTORE: { "연구 항목 복원": "Research item restored" },
  RESULT: {
    "연구 진행 중간 저장": "Research checkpoint",
    "당시 연구 답변": "Saved research answer",
  },
};
const titlesZh: Record<string, Readonly<Record<string, string>>> = {
  REVISION: {
    "결정 객체 변경": "决策对象已更新",
    "근거 충분성 평가 변경": "证据充分性评估已更新",
    "가설 변경": "假设已更新",
    "가설 구성 변경": "假设组合已更新",
    "행동 제안 변경": "行动建议已更新",
    "행동 구성 변경": "行动组合已更新",
    "행동 계획 변경": "行动计划已更新",
    "근거 연결 변경": "证据链接已更新",
    "기억 변경": "记忆已更新",
    "연구 항목 변경": "研究项目已更新",
  },
  MEMORY: { "연구 기억 기록": "已记录研究记忆" },
  RESTORE: { "연구 항목 복원": "研究项目已恢复" },
  RESULT: { "연구 진행 중간 저장": "研究检查点", "당시 연구 답변": "已保存的研究回答" },
};

const titlesZhHant: Record<string, Readonly<Record<string, string>>> = {
  REVISION: {
    "결정 객체 변경": "決策物件已更新",
    "근거 충분성 평가 변경": "證據充分性評估已更新",
    "가설 변경": "假設已更新",
    "가설 구성 변경": "假設組合已更新",
    "행동 제안 변경": "行動建議已更新",
    "행동 구성 변경": "行動組合已更新",
    "행동 계획 변경": "行動計劃已更新",
    "근거 연결 변경": "證據連結已更新",
    "기억 변경": "記憶已更新",
    "연구 항목 변경": "研究專案已更新",
  },
  MEMORY: { "연구 기억 기록": "已記錄研究記憶" },
  RESTORE: { "연구 항목 복원": "研究專案已恢復" },
  RESULT: { "연구 진행 중간 저장": "研究檢查點", "당시 연구 답변": "已儲存的研究回答" },
};
const titlesJa: Record<string, Readonly<Record<string, string>>> = {
  REVISION: {
    "결정 객체 변경": "判断の対象を更新しました",
    "근거 충분성 평가 변경": "根拠の十分さの評価を更新しました",
    "가설 변경": "仮説を更新しました",
    "가설 구성 변경": "仮説の構成を更新しました",
    "행동 제안 변경": "行動の提案を更新しました",
    "행동 구성 변경": "行動の構成を更新しました",
    "행동 계획 변경": "行動計画を更新しました",
    "근거 연결 변경": "根拠のリンクを更新しました",
    "기억 변경": "記憶を更新しました",
    "연구 항목 변경": "調査項目を更新しました",
  },
  MEMORY: { "연구 기억 기록": "調査の記憶を記録しました" },
  RESTORE: { "연구 항목 복원": "調査項目を復元しました" },
  RESULT: {
    "연구 진행 중간 저장": "調査のチェックポイント",
    "당시 연구 답변": "保存された調査の回答",
  },
};
/** The server writes these titles in Korean; the one that names an internal object is said plainly. */
const titlesKo: Record<string, Readonly<Record<string, string>>> = {
  REVISION: { "결정 객체 변경": "결정 변경" },
};
const titles = {
  ko: titlesKo,
  en: titlesEn,
  "zh-Hans": titlesZh,
  "zh-Hant": titlesZhHant,
  ja: titlesJa,
} as const;

export function historyTitle(item: { kind: string; title: string }): string {
  const locale = getLocale();
  return titles[locale][item.kind]?.[item.title] ?? item.title;
}
