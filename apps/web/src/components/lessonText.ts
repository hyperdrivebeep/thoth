import { message as formatMessage } from "../i18n/formatMessages";
import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
import type { LessonItem } from "../api/lessons";
import { CLOSURE_KINDS, eliminationLine, matchLabel } from "./judgmentRecordText";
import { actorLabel, timeText, verdictLook } from "./traceText";

/** Plain words for a lesson: what a rule computed or a person recorded earlier, in the same condition. Never advice, never a count of how often. */

export const LESSON_NOTE = messages["lesson.notice"].ko;

export function withheldLine(stale: number, refuted: number): string | null {
  if (stale === 0 && refuted === 0) return null;
  return formatMessage("withheldLessons", stale, refuted);
}

export function lessonDisplay(item: LessonItem): { title: string; lines: string[] } {
  const observed = (item.detail.observations ?? []).map(entry => formatMessage("observation", entry.observation, entry.evidence_refs.join(", ")));
  const who = formatMessage("closureRecordedBy", actorLabel(item.actor_id), timeText(item.created_at));
  const original = item.detail.original_state ? [formatMessage("closureOriginalVerdict", verdictLook(item.subject_kind, item.detail.original_state).text)] : [];
  const basis = item.detail.basis_ref ? [formatMessage("closureBasis", item.detail.basis_ref)] : [];
  if (item.kind === "TEST_RESULT") return { title: formatMessage("pastTest", matchLabel(item.outcome as never)), lines: [...observed, who] };
  if (item.kind === "ELIMINATION") return { title: formatMessage("pastExclusion", eliminationLine(item.outcome as never) ?? t("activity.excluded")), lines: [...observed, who] };
  if (item.kind === "EFFECT_CONFIRMED") return { get title() { return t("lesson.effect"); }, lines: [...basis, ...original] };
  const label = CLOSURE_KINDS.find(entry => entry.code === item.detail.closure_kind)?.label ?? t("lesson.disposition");
  return { title: item.detail.closure_kind === "CONDITION_CHANGED" ? formatMessage("conditionScope", label, item.detail.scope ?? t("judgment.unspecified")) : label, lines: [...basis, ...original, who] };
}
