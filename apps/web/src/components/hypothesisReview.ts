import { objectList, objectValue, stringValues, textValue } from "../api/presentation";

/** The model's review of each hypothesis, as the result records it, and what the screen derives from it. */
export type ReviewRelation = "SUPPORTED" | "QUALIFIED" | "UNSUPPORTED" | "INCONCLUSIVE";
export type ReviewView = { relation: ReviewRelation; explanation: string; gaps: string[]; evidenceRefs: string[] };

const RELATION_WORD: Record<ReviewRelation, string> = { SUPPORTED: "지지", QUALIFIED: "조건부 지지", UNSUPPORTED: "불지지", INCONCLUSIVE: "결론 불가" };
const isRelation = (value: string): value is ReviewRelation => Object.hasOwn(RELATION_WORD, value);
export const reviewWord = (relation: ReviewRelation) => RELATION_WORD[relation];

/** Review decisions by hypothesis id. A decision with an unknown relation is left out, never guessed. */
export function reviewDecisions(result: Record<string, unknown>): Map<string, ReviewView> {
  const found = new Map<string, ReviewView>();
  for (const decision of objectList(objectValue(result.hypothesis_review).decisions)) {
    const relation = textValue(decision.relation);
    const id = textValue(decision.hypothesis_id);
    if (!id || !isRelation(relation)) continue;
    found.set(id, { relation, explanation: textValue(decision.explanation), gaps: stringValues(decision.gaps), evidenceRefs: stringValues(decision.evidence_refs) });
  }
  return found;
}

/**
 * True when a review was recorded and none of its decisions supports a hypothesis (not even conditionally).
 * No recorded review says nothing: an old record is not "unconfirmed", it is unreviewed.
 */
export function causeUnconfirmed(result: Record<string, unknown>): boolean {
  const decisions = Array.from(reviewDecisions(result).values());
  return decisions.length > 0 && !decisions.some(item => item.relation === "SUPPORTED" || item.relation === "QUALIFIED");
}

const STATUS_WORD: Record<string, string> = {
  DRAFT: "초안", GROUNDED_CANDIDATE: "근거가 있는 후보", COUNTEREVIDENCE_CHECKED: "반대 근거를 확인함", PREDICTION_BOUND: "예상 관찰을 정함",
  TESTABLE: "시험할 수 있음", EMPIRICALLY_UPDATED: "시험 결과를 반영함", INCONCLUSIVE: "결론을 내지 못함", ELIMINATED_WITHIN_SCOPE: "이 범위에서는 배제됨",
};
/** Plain words for a hypothesis status; a status this screen does not know is not shown. */
export const hypothesisStatusWord = (status: string): string | null => STATUS_WORD[status] ?? null;
