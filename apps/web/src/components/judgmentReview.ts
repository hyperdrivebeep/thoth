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
  { code: "EVIDENCE_INTERPRETATION", label: "근거 해석이 다름" },
  { code: "MISSING_KEY_EVIDENCE", label: "중요한 근거가 빠짐" },
  { code: "SOURCE_OR_TIME_INAPPROPRIATE", label: "출처·시점이 부적절함" },
  { code: "WRONG_RELATION_TO_OTHER_HYPOTHESES", label: "다른 가설과의 관계가 잘못됨" },
  { code: "INSUFFICIENT_EXPLANATION", label: "설명이 부족함" },
  { code: "OTHER", label: "기타" },
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

const outcomes: Record<string, string> = { UPHELD: "유지", CHANGED: "변경", HOLD: "보류" };
export const outcomeLabel = (value: string) => outcomes[value] ?? value;

const simple: Record<string, string> = { SUPPORT: "지지", COUNTER: "반박", UNASSESSED: "미평가", REMOVED: "이번 결과에 없음" };
/** Relation text as recorded ("SUPPORT" or "SUPPORT:3|COUNTER:0|APPRAISAL:x") in plain Korean. */
export function relationText(value: string): string {
  if (simple[value]) return simple[value];
  const support = /SUPPORT:(\d+)/.exec(value)?.[1];
  const counter = /COUNTER:(\d+)/.exec(value)?.[1];
  return support !== undefined && counter !== undefined ? `뒷받침 ${support} · 반박 ${counter}` : value;
}
