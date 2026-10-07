import type { LessonItem } from "../api/lessons";
import { CLOSURE_KINDS, eliminationLine, matchLabel } from "./judgmentRecordText";
import { actorLabel, timeText, verdictLook } from "./traceText";

/** Plain words for a lesson: what a rule computed or a person recorded earlier, in the same condition. Never advice, never a count of how often. */

export const LESSON_NOTE = "같은 기준, 같은 측정 단위, 같은 조건, 같은 규칙 개정에서 이전에 규칙이 계산했거나 사람이 기록한 결과입니다. 참고로만 보이며 시험의 순서에 쓰이지 않습니다. 이번에도 같은 결과가 나온다는 뜻이 아닙니다.";

export function withheldLine(stale: number, refuted: number): string | null {
  if (stale === 0 && refuted === 0) return null;
  const parts = [stale > 0 ? "낡음 " + stale + "개" : "", refuted > 0 ? "반박됨 " + refuted + "개" : ""].filter(Boolean).join(" · ");
  return "원본 기록은 남아 있습니다. 근거가 바뀌었거나 반대 결과가 나와 뺀 교훈은 보이지 않습니다(" + parts + ").";
}

export function lessonDisplay(item: LessonItem): { title: string; lines: string[] } {
  const observed = (item.detail.observations ?? []).map(entry => "관찰: " + entry.observation + (entry.evidence_refs.length > 0 ? " (근거 참조 " + entry.evidence_refs.join(", ") + ")" : ""));
  const who = actorLabel(item.actor_id) + " 기록 · " + timeText(item.created_at);
  const original = item.detail.original_state ? ["원래 판정: " + verdictLook(item.subject_kind, item.detail.original_state).text] : [];
  const basis = item.detail.basis_ref ? ["근거 문서: " + item.detail.basis_ref] : [];
  if (item.kind === "TEST_RESULT") return { title: "이전 시험 결과: " + matchLabel(item.outcome as never), lines: [...observed, who] };
  if (item.kind === "ELIMINATION") return { title: "이전 조사에서 한 가설이 " + (eliminationLine(item.outcome as never) ?? "배제") + "로 표시되었습니다", lines: [...observed, who] };
  if (item.kind === "EFFECT_CONFIRMED") return { title: "수정을 반영한 뒤 새 결과로 이 줄이 충족으로 바뀐 적이 있습니다(규칙이 계산한 값)", lines: [...basis, ...original] };
  const label = CLOSURE_KINDS.find(entry => entry.code === item.detail.closure_kind)?.label ?? "처분 기록";
  return { title: item.detail.closure_kind === "CONDITION_CHANGED" ? label + "(조건 범위: " + (item.detail.scope ?? "미기재") + ")" : label, lines: [...basis, ...original, who] };
}
