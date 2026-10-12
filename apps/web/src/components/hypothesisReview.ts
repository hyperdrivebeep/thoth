import { t } from "../i18n/locale";
import { objectList, objectValue, stringValues, textValue } from "../api/presentation";

/** The model's review of each hypothesis, as the result records it, and what the screen derives from it. */
export type ReviewRelation = "SUPPORTED" | "QUALIFIED" | "UNSUPPORTED" | "INCONCLUSIVE";
export type ReviewView = { relation: ReviewRelation; explanation: string; gaps: string[]; evidenceRefs: string[] };

const RELATION_WORD: Record<ReviewRelation, string> = { get SUPPORTED() { return t("hypotheses.support"); }, get QUALIFIED() { return t("review.qualified"); }, get UNSUPPORTED() { return t("review.unsupported"); }, get INCONCLUSIVE() { return t("review.inconclusive"); } };
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
  get DRAFT() { return t("review.draft"); }, get GROUNDED_CANDIDATE() { return t("review.grounded"); }, get COUNTEREVIDENCE_CHECKED() { return t("review.counterChecked"); }, get PREDICTION_BOUND() { return t("review.predictionBound"); },
  get TESTABLE() { return t("review.testable"); }, get EMPIRICALLY_UPDATED() { return t("review.resultsApplied"); }, get INCONCLUSIVE() { return t("review.noConclusion"); }, get ELIMINATED_WITHIN_SCOPE() { return t("review.excluded"); },
};
/** Plain words for a hypothesis status; a status this screen does not know is not shown. */
export const hypothesisStatusWord = (status: string): string | null => STATUS_WORD[status] ?? null;
