import { messages } from "../i18n/messages";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { ClosureEvent, ClosureKind, ClosureRow, DiscriminationItem, MatchKind } from "../api/judgmentRecords";
import { actorLabel, timeText, verdictLook } from "./traceText";

/** Plain words for what a person recorded: a test result, a refutation condition, or a closure of a trace row that was not met. */

export const MATCH_KINDS: { code: MatchKind; label: string }[] = [
  { code: "THIS_HYPOTHESIS", get label() { return t("judgment.this"); } },
  { code: "ALTERNATIVE", get label() { return t("judgment.alternative"); } },
  { code: "NEITHER", get label() { return t("judgment.neither"); } },
  { code: "UNDETERMINED", get label() { return t("judgment.undetermined"); } },
];
export const matchLabel = (code: MatchKind) => MATCH_KINDS.find(item => item.code === code)?.label ?? t("judgment.recorded");

/** The mark on a hypothesis whose test results fit the other explanation. Never a deletion; the results can be recorded again. */
export function eliminationLine(state: DiscriminationItem["elimination"]): string | null {
  if (state === "SINGLE") return t("judgment.excludedOnce");
  if (state === "REPEATED") return t("judgment.excludedRepeated");
  return null;
}
export const ELIMINATION_NOTE = messages["judgment.eliminationNote"].ko;
export const eliminationNote = () => t("judgment.eliminationNote");

const STANDING_WORDS: Record<string, string> = { get FITS() { return t("judgment.this"); }, get AGAINST_ONCE() { return t("judgment.againstOnce"); }, get AGAINST_REPEATED() { return t("judgment.againstRepeated"); }, get MIXED() { return t("judgment.mixed"); } };
/** The line on a hypothesis about the test results people recorded for it; null when none counts. */
export const standingLine = (standing: DiscriminationItem["standing"]) => (standing && STANDING_WORDS[standing] ? message("standingResults", STANDING_WORDS[standing]) : null);
export const STANDING_NOTE = messages["judgment.standingNote"].ko;
export const standingNote = () => t("judgment.standingNote");
const againstStanding = (standing: DiscriminationItem["standing"]) => standing === "AGAINST_ONCE" || standing === "AGAINST_REPEATED";

/** The note on an action that rests on a hypothesis the recorded results went against; information only, nothing is held back. */
export function standingActionNote(hypothesisIds: string[] | undefined, items: DiscriminationItem[] | undefined): string | null {
  const found = (items ?? []).some(item => againstStanding(item.standing) && (hypothesisIds ?? []).includes(item.hypothesis_id));
  return found ? t("judgment.againstNote") : null;
}

export const CLOSURE_KINDS: { code: ClosureKind; label: string; hint: string }[] = [
  { code: "FIX_APPLIED", get label() { return t("judgment.fixApplied"); }, get hint() { return t("judgment.fixHint"); } },
  { code: "HUMAN_CLOSED", get label() { return t("judgment.humanClosed"); }, get hint() { return t("judgment.humanHint"); } },
  { code: "WAIVER_RECORDED", get label() { return t("judgment.waiver"); }, get hint() { return t("judgment.waiverHint"); } },
  { code: "CONDITION_CHANGED", get label() { return t("judgment.conditionChanged"); }, get hint() { return t("judgment.conditionHint"); } },
];
export const CLOSURE_NOTE = messages["judgment.closureNote"].ko;
export const closureNote = () => t("judgment.closureNote");


const MET = new Set(["PASS_COMPUTED", "PASS"]);
export const isMet = (state: string | null | undefined) => state != null && MET.has(state);

/** What one recorded closure is called: its kind, with the condition's range for a changed operating condition. */
export function eventKindLabel(event: ClosureEvent): string {
  return event.kind === "CONDITION_CHANGED" ? message("closureCondition", event.scope ?? t("judgment.unspecified")) : CLOSURE_KINDS.find(item => item.code === event.kind)?.label ?? t("judgment.closureRecorded");
}

/** One record as short lines: the verdict it was written against, who and when, its document and note. */
export function eventLines(subjectKind: ClosureRow["subject_kind"], event: ClosureEvent): string[] {
  const lines = [message("closureOriginalVerdict", verdictLook(subjectKind, event.verdict_state).text), message("closureRecordedBy", actorLabel(event.actor_id), timeText(event.created_at)), message("closureBasis", event.basis_ref)];
  if (event.note) lines.push(event.note);
  return lines;
}

/** The records before the latest one, newest first. */
export function previousEvents(row: ClosureRow): ClosureEvent[] {
  return row.events.slice(0, -1).reverse();
}

/** What to show beside a row's verdict: a label and short lines. The effect-confirmed mark is the rule's; the others are a person's. */
export function closureDisplay(row: ClosureRow): { label: string; lines: string[] } {
  const latest = row.events[row.events.length - 1];
  const label = row.effect_confirmed ? t("judgment.effectConfirmed") : eventKindLabel(latest);
  const lines: string[] = [];
  if (row.effect_confirmed) lines.push(t("judgment.effectNotice"));
  lines.push(...eventLines(row.subject_kind, latest));
  if (!row.effect_confirmed && latest.kind === "WAIVER_RECORDED") lines.push(t("judgment.externalWaiver"));
  if (!row.effect_confirmed && row.verdict_changed_since) lines.push(t("judgment.verdictChanged"));
  return { label, lines };
}
