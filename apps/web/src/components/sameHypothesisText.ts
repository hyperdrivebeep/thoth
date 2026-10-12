import { message as formatMessage } from "../i18n/formatMessages";
import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
import type { HypothesisLink } from "../api/hypothesisLink";
import type { SameView } from "../api/hypothesisSame";
import { timeText } from "./traceText";

/** Plain words for hypotheses a person marked as the same one across investigations. A reference only, never an addition of counts. */

export const SAME_BUTTON = messages["sameHypothesis.mark"].ko;
export const SAME_NOTE = messages["sameHypothesis.notice"].ko;
export const SAME_TAG = messages["sameHypothesis.title"].ko;
export const NO_RESULTS = messages["sameHypothesis.noResults"].ko;

export function sameRefusal(message: string): string {
  if (message.includes("SAME_ALREADY_LINKED")) return t("sameHypothesis.already");
  if (message.includes("SAME_NOT_INVESTIGATION")) return t("sameHypothesis.generatedOnly");
  if (message.includes("SAME_NOT_LINKED")) return t("sameHypothesis.disconnected");
  return t("sameHypothesis.failed");
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
  const when = member.investigated_at ? formatMessage("investigatedAt", timeText(member.investigated_at)) : t("sameHypothesis.unknownDate");
  return clip(member.statement ?? t("sameHypothesis.unreadable"), 80) + " · " + when;
}
