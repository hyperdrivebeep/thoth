import type { HypothesisLink, RecheckReasonCode } from "../api/hypothesisLink";
export type { HypothesisLink, RecheckReasonCode } from "../api/hypothesisLink";
import { actorLabel, timeText, verdictLook } from "./traceText";

/** Plain words for a hypothesis that came from a trace row and what happened to that row's verdict. */

export const RECHECK_REASONS: { code: RecheckReasonCode; label: string; hint: string }[] = [
  { code: "UNRELATED", label: "판정이 바뀌었지만 이 가설과 관계없음", hint: "이 가설은 바뀐 부분과 상관이 없다고 봅니다." },
  { code: "STILL_MATCHES", label: "새 결과도 이 가설과 맞음(근거 확인함)", hint: "바뀐 결과를 직접 보고, 가설이 그대로 맞는다고 확인했습니다." },
  { code: "NEEDS_RESEARCH", label: "다시 조사가 필요함", hint: "이 가설은 계속 '이전 근거 기준'으로 남고, 새로 조사해야 한다고 적어 둡니다." },
  { code: "OTHER", label: "기타", hint: "위에 없는 이유입니다. 이유를 글로 적어야 합니다." },
];
const reasonLabel = (code: RecheckReasonCode) => RECHECK_REASONS.find(item => item.code === code)?.label ?? "이유 기록됨";

/** Free words are needed for "other", and to keep a link across a met/failed swap; asking for more research needs none. */
export const noteRequired = (reason: RecheckReasonCode, flipped: boolean) => reason === "OTHER" || (flipped && reason !== "NEEDS_RESEARCH");

const verdictText = (link: HypothesisLink, state: string | null) => (state === null ? "" : verdictLook(link.subject_kind, state).text);

/** The line for a hypothesis whose verdict changed; null when it is current or a person kept it. */
export function linkChangeLine(link: HypothesisLink): string | null {
  if (link.state !== "STALE") return null;
  if (link.change === "SUBJECT_MISSING") return `이전 근거 기준 — 이 가설이 나온 추적표 줄이 지금은 없습니다(이전: ${verdictText(link, link.link_state)})`;
  if (link.change === "EVIDENCE_ONLY") return `이전 근거 기준 — 판정 상태는 같고 근거만 바뀌었습니다(판정: ${verdictText(link, link.current_state)})`;
  const swap = link.change === "FLIPPED" ? " 충족과 미달이 뒤바뀌었습니다." : "";
  return `이전 근거 기준 — 판정이 바뀌었습니다(이전: ${verdictText(link, link.link_state)} → 지금: ${verdictText(link, link.current_state)})${swap}`;
}

/** Who looked at this change and why, when somebody did. */
export function recheckLine(link: HypothesisLink): string | null {
  const event = link.recheck;
  if (!event) return null;
  const who = `${actorLabel(event.actor_id)} · ${timeText(event.created_at)}`;
  if (link.state === "RECHECKED") return `다시 확인됨 — ${reasonLabel(event.reason_code)} · ${who}${event.note ? `: ${event.note}` : ""}`;
  return `이유 기록: ${reasonLabel(event.reason_code)} · ${who}. 이 가설은 이전 근거 기준으로 남습니다.`;
}

/** Old hypotheses of the same row that stand on the same new verdict, so one decision can cover them. */
export function linkSiblings(link: HypothesisLink, all: HypothesisLink[]): HypothesisLink[] {
  return all.filter(item => item.hypothesis_id !== link.hypothesis_id && item.state === "STALE" && item.subject_kind === link.subject_kind
    && item.subject_id === link.subject_id && item.current_verdict_revision === link.current_verdict_revision);
}

export function staleCount(links: HypothesisLink[] | undefined, kind: string, subjectId: string): number {
  return (links ?? []).filter(item => item.state === "STALE" && item.subject_kind === kind && item.subject_id === subjectId).length;
}

/** The note on an action that rests on hypotheses whose verdict changed; null when none of them did. */
export function staleActionNote(hypothesisIds: string[] | undefined, links: HypothesisLink[] | undefined): string | null {
  const stale = (links ?? []).filter(item => item.state === "STALE" && (hypothesisIds ?? []).includes(item.hypothesis_id));
  if (stale.length === 0) return null;
  const same = stale.every(item => item.change === "EVIDENCE_ONLY") ? "(판정 상태는 같고 근거만 바뀜)" : "";
  return `이 행동이 기대는 가설 ${stale.length}개가 이전 근거 기준입니다${same}. 사람이 가설을 다시 확인하기 전에는 이 행동의 승인 요청과 결정, 실행이 막힙니다.`;
}
