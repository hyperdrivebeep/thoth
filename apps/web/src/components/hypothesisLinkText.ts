import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { HypothesisLink, RecheckReasonCode } from "../api/hypothesisLink";
export type { HypothesisLink, RecheckReasonCode } from "../api/hypothesisLink";
import { actorLabel, timeText, verdictLook } from "./traceText";

/** Plain words for a hypothesis that came from a trace row and what happened to that row's verdict. */

export const RECHECK_REASONS: { code: RecheckReasonCode; label: string; hint: string }[] = [
  { code: "UNRELATED", get label() { return t("hypothesisLink.unrelated"); }, get hint() { return t("hypothesisLink.unrelatedHint"); } },
  { code: "STILL_MATCHES", get label() { return t("hypothesisLink.supports"); }, get hint() { return t("hypothesisLink.supportsHint"); } },
  { code: "NEEDS_RESEARCH", get label() { return t("hypothesisLink.investigate"); }, get hint() { return t("hypothesisLink.investigateHint"); } },
  { code: "OTHER", get label() { return t("hypothesisLink.other"); }, get hint() { return t("hypothesisLink.otherHint"); } },
];
const reasonLabel = (code: RecheckReasonCode) => RECHECK_REASONS.find(item => item.code === code)?.label ?? t("hypothesisLink.reasonRecorded");

/** Free words are needed for "other", and to keep a link across a met/failed swap; asking for more research needs none. */
export const noteRequired = (reason: RecheckReasonCode, flipped: boolean) => reason === "OTHER" || (flipped && reason !== "NEEDS_RESEARCH");

const verdictText = (link: HypothesisLink, state: string | null) => (state === null ? "" : verdictLook(link.subject_kind, state).text);

/** The line for a hypothesis whose verdict changed; null when it is current or a person kept it. */
export function linkChangeLine(link: HypothesisLink): string | null {
  if (link.state !== "STALE") return null;
  if (link.change === "SUBJECT_MISSING") return formatMessage("staleMissing", verdictText(link, link.link_state));
  if (link.change === "EVIDENCE_ONLY") return formatMessage("staleEvidence", verdictText(link, link.current_state));
  return formatMessage("staleVerdict", verdictText(link, link.link_state), verdictText(link, link.current_state), link.change === "FLIPPED");
}

/** Who looked at this change and why, when somebody did. */
export function recheckLine(link: HypothesisLink): string | null {
  const event = link.recheck;
  if (!event) return null;
  const who = `${actorLabel(event.actor_id)} · ${timeText(event.created_at)}`;
  if (link.state === "RECHECKED") return formatMessage("rechecked", reasonLabel(event.reason_code), who, event.note);
  return formatMessage("recheckRecorded", reasonLabel(event.reason_code), who);
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
  const same = stale.every(item => item.change === "EVIDENCE_ONLY");
  return formatMessage("staleAction", stale.length, same);
}
