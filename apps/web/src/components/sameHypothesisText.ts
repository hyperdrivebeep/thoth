import type { HypothesisLink } from "../api/hypothesisLink";
import type { SameView } from "../api/hypothesisSame";
import { timeText } from "./traceText";

/** Plain words for hypotheses a person marked as the same one across investigations. A reference only, never an addition of counts. */

export const SAME_BUTTON = "다른 조사의 같은 가설로 표시";
export const SAME_NOTE = "사람이 같은 가설이라고 표시한 것입니다. 이전 조사의 결과는 참고로만 보이고, 이 가설의 배제나 시험 순서에 더해지지 않습니다.";
export const SAME_TAG = "다른 조사의 같은 가설";
export const NO_RESULTS = "기록된 시험 결과 없음";

export function sameRefusal(message: string): string {
  if (message.includes("SAME_ALREADY_LINKED")) return "이미 같은 가설로 표시되어 있습니다.";
  if (message.includes("SAME_NOT_INVESTIGATION")) return "조사에서 나온 가설끼리만 같은 가설로 표시할 수 있습니다.";
  if (message.includes("SAME_NOT_LINKED")) return "이미 연결이 끊어져 있습니다. 화면을 새로 읽어 주세요.";
  return "표시하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}

const clip = (text: string, max: number) => (text.length > max ? text.slice(0, max - 1) + "…" : text);

/** The other hypotheses of the project that came from an investigation, the same trace row first; none already in this group, never itself. */
export function sameCandidates(hypothesisId: string, links: HypothesisLink[] | undefined, same: SameView | undefined): HypothesisLink[] {
  const mine = new Set(same?.groups.find(group => group.includes(hypothesisId)) ?? []);
  const own = (links ?? []).find(item => item.hypothesis_id === hypothesisId);
  const others = (links ?? []).filter(item => item.hypothesis_id !== hypothesisId && !mine.has(item.hypothesis_id));
  const rowOf = (item: HypothesisLink) => own !== undefined && item.subject_kind === own.subject_kind && item.subject_id === own.subject_id;
  return [...others.filter(rowOf), ...others.filter(item => !rowOf(item))];
}

export const candidateLabel = (item: HypothesisLink) => clip(item.statement, 60) + " · " + (item.subject_title || item.subject_id);

/** The line about a hypothesis marked as the same one: what it says and when it was investigated. */
export function memberLine(same: SameView, id: string): string {
  const member = same.members[id] ?? {};
  const when = member.investigated_at ? timeText(member.investigated_at) + " 조사" : "조사 날짜 미확인";
  return clip(member.statement ?? "읽을 수 없는 가설", 80) + " · " + when;
}
