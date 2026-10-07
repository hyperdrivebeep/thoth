import type { ClosureEvent, ClosureKind, ClosureRow, DiscriminationItem, MatchKind } from "../api/judgmentRecords";
import { actorLabel, timeText, verdictLook } from "./traceText";

/** Plain words for what a person recorded: a test result, a refutation condition, or a closure of a trace row that was not met. */

export const MATCH_KINDS: { code: MatchKind; label: string }[] = [
  { code: "THIS_HYPOTHESIS", label: "이 가설과 맞음" },
  { code: "ALTERNATIVE", label: "다른 설명과 맞음" },
  { code: "NEITHER", label: "둘 다 아님" },
  { code: "UNDETERMINED", label: "판단 불가" },
];
export const matchLabel = (code: MatchKind) => MATCH_KINDS.find(item => item.code === code)?.label ?? "기록됨";

/** The mark on a hypothesis whose test results fit the other explanation. Never a deletion; the results can be recorded again. */
export function eliminationLine(state: DiscriminationItem["elimination"]): string | null {
  if (state === "SINGLE") return "배제(결과 1건)";
  if (state === "REPEATED") return "배제(반복 확인)";
  return null;
}
export const ELIMINATION_NOTE = "기록된 시험 결과가 다른 설명과 맞았다는 표시입니다. 가설은 지워지지 않고, 결과를 다시 기록하면 달라집니다.";

const STANDING_WORDS: Record<string, string> = { FITS: "이 가설과 맞음", AGAINST_ONCE: "다른 설명과 맞음(1건)", AGAINST_REPEATED: "다른 설명과 맞음(반복)", MIXED: "엇갈림" };
/** The line on a hypothesis about the test results people recorded for it; null when none counts. */
export const standingLine = (standing: DiscriminationItem["standing"]) => (standing && STANDING_WORDS[standing] ? `기록된 시험 결과: ${STANDING_WORDS[standing]}` : null);
export const STANDING_NOTE = "사람이 기록한 시험 결과를 읽은 것이며, 봉인된 시험으로 얻은 검증 상태를 바꾸지 않습니다.";
const againstStanding = (standing: DiscriminationItem["standing"]) => standing === "AGAINST_ONCE" || standing === "AGAINST_REPEATED";
const AGAINST_NOTE = "이 행동이 기대는 가설이 기록된 시험 결과에서 다른 설명과 맞았습니다. 이 안내는 승인과 실행을 막지 않습니다. 시험 결과를 다시 살펴보고 판단하세요.";
/** The note on an action that rests on a hypothesis the recorded results went against; information only, nothing is held back. */
export function standingActionNote(hypothesisIds: string[] | undefined, items: DiscriminationItem[] | undefined): string | null {
  const found = (items ?? []).some(item => againstStanding(item.standing) && (hypothesisIds ?? []).includes(item.hypothesis_id));
  return found ? AGAINST_NOTE : null;
}

export const CLOSURE_KINDS: { code: ClosureKind; label: string; hint: string }[] = [
  { code: "FIX_APPLIED", label: "수정 반영됨(효과 미확인)", hint: "고친 내용을 반영했지만 새 결과로 효과를 확인하지는 않았습니다. 새 결과가 들어와 이 줄이 충족으로 바뀌면 규칙이 '효과 확인됨'을 계산합니다." },
  { code: "HUMAN_CLOSED", label: "사람 확인 종결(시험 결과 없음)", hint: "시험 결과 없이 사람이 확인해 닫았습니다." },
  { code: "WAIVER_RECORDED", label: "편차·면제 승인(원 기준 미충족 유지)", hint: "시스템 밖에서 내려진 편차·면제 승인을 기록합니다. 원래 기준은 미충족으로 남습니다." },
  { code: "CONDITION_CHANGED", label: "운용 조건 변경", hint: "기준이 적용되는 운용 조건이 바뀐 것을 기록합니다. 어느 조건 범위인지 적어야 합니다." },
];
export const CLOSURE_NOTE = "사람이 시스템 밖에서 내린 처분을 적어 두는 기록입니다. THOTH가 면제나 승인을 내리지 않으며, 이 줄의 판정(규칙이 계산한 값)은 그대로입니다.";
const EFFECT_LABEL = "효과 확인됨";

const MET = new Set(["PASS_COMPUTED", "PASS"]);
export const isMet = (state: string | null | undefined) => state != null && MET.has(state);

/** What one recorded closure is called: its kind, with the condition's range for a changed operating condition. */
export function eventKindLabel(event: ClosureEvent): string {
  return event.kind === "CONDITION_CHANGED" ? `운용 조건 변경(조건 범위: ${event.scope ?? "미기재"})` : CLOSURE_KINDS.find(item => item.code === event.kind)?.label ?? "처분 기록됨";
}

/** One record as short lines: the verdict it was written against, who and when, its document and note. */
export function eventLines(subjectKind: ClosureRow["subject_kind"], event: ClosureEvent): string[] {
  const lines = [`원래 판정: ${verdictLook(subjectKind, event.verdict_state).text}`, `${actorLabel(event.actor_id)} 기록 · ${timeText(event.created_at)}`, `근거 문서: ${event.basis_ref}`];
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
  const label = row.effect_confirmed ? EFFECT_LABEL : eventKindLabel(latest);
  const lines: string[] = [];
  if (row.effect_confirmed) lines.push("수정을 기록한 뒤 들어온 새 결과로 이 줄이 충족으로 바뀌었습니다(규칙이 계산한 값).");
  lines.push(...eventLines(row.subject_kind, latest));
  if (!row.effect_confirmed && latest.kind === "WAIVER_RECORDED") lines.push("시스템 밖에서 내려진 처분을 적어 둔 기록입니다. THOTH가 면제를 내린 것이 아닙니다.");
  if (!row.effect_confirmed && row.verdict_changed_since) lines.push("이 기록 뒤에 판정이 바뀌었습니다. 기록은 그때의 판정에 대한 것입니다.");
  return { label, lines };
}
