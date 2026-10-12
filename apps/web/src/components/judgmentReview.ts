import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { objectList, objectValue, stringValues, textValue } from "../api/presentation";

export type ReviewRequest = {
  request_id: string; hypothesis_id: string; evidence_ref: string | null; status: string; instruction_state: string;
  instruction_status: string | null; instruction_failure: string | null; reason_codes: string[]; note: string;
  hypothesis_revision_digest: string; added_evidence_refs: string[]; created_at: string;
  resolution: { outcome: string; previous_relation: string; resulting_relation: string } | null;
};
export type ReviewTarget = { hypothesisId: string; statement: string; evidenceRef: string | null; evidenceLabel: string };
export type ReviewControls = {
  requests: ReviewRequest[];
  onRequest: (target: ReviewTarget) => void;
  onResend: (request: ReviewRequest) => void;
};

export const reviewReasons = [
  { code: "EVIDENCE_INTERPRETATION", get label() { return t("review.interpretation"); } },
  { code: "MISSING_KEY_EVIDENCE", get label() { return t("review.missing"); } },
  { code: "SOURCE_OR_TIME_INAPPROPRIATE", get label() { return t("review.source"); } },
  { code: "WRONG_RELATION_TO_OTHER_HYPOTHESES", get label() { return t("review.relationship"); } },
  { code: "INSUFFICIENT_EXPLANATION", get label() { return t("review.explanation"); } },
  { code: "OTHER", get label() { return t("hypothesisLink.other"); } },
] as const;

export function parseRequests(value: unknown): ReviewRequest[] {
  return objectList(value).map(item => {
    const resolution = objectValue(item.resolution);
    return {
      request_id: textValue(item.request_id), hypothesis_id: textValue(item.hypothesis_id), evidence_ref: textValue(item.evidence_ref) || null,
      status: textValue(item.status), instruction_state: textValue(item.instruction_state), instruction_status: textValue(item.instruction_status) || null,
      instruction_failure: textValue(item.instruction_failure) || null, reason_codes: stringValues(item.reason_codes), note: textValue(item.note),
      hypothesis_revision_digest: textValue(item.hypothesis_revision_digest), added_evidence_refs: stringValues(item.added_evidence_refs),
      created_at: textValue(item.created_at),
      resolution: textValue(resolution.outcome) ? { outcome: textValue(resolution.outcome), previous_relation: textValue(resolution.previous_relation),
        resulting_relation: textValue(resolution.resulting_relation) } : null,
    };
  });
}

const newest = (items: ReviewRequest[]) => [...items].sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1);
/** A hypothesis has at most one open request at a time, whichever evidence it was raised on. */
export const openRequest = (requests: ReviewRequest[], hypothesisId: string) =>
  newest(requests.filter(item => item.hypothesis_id === hypothesisId && ["OPEN", "REVIEWING"].includes(item.status)));
export const resolvedRequest = (requests: ReviewRequest[], hypothesisId: string) =>
  newest(requests.filter(item => item.hypothesis_id === hypothesisId && item.status === "RESOLVED"));

const outcomes: Record<string, string> = { get UPHELD() { return t("review.keep"); }, get CHANGED() { return t("review.change"); }, get HOLD() { return t("state.hold"); } };
export const outcomeLabel = (value: string) => outcomes[value] ?? value;

const simple: Record<string, string> = { get SUPPORT() { return t("hypotheses.support"); }, get COUNTER() { return t("hypotheses.refute"); }, get UNASSESSED() { return t("review.unassessed"); }, get REMOVED() { return t("review.absent"); } };
/** Relation text as recorded ("SUPPORT" or "SUPPORT:3|COUNTER:0|APPRAISAL:x") in plain Korean. */
export function relationText(value: string): string {
  if (simple[value]) return simple[value];
  const support = /SUPPORT:(\d+)/.exec(value)?.[1];
  const counter = /COUNTER:(\d+)/.exec(value)?.[1];
  return support !== undefined && counter !== undefined ? formatMessage("supportCounts", support, counter) : value;
}
