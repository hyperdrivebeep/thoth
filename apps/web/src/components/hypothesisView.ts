import { objectList, objectValue, stringValues, textValue } from "../api/presentation";
import { reviewDecisions, type ReviewView } from "./hypothesisReview";

export type HypothesisRow = {
  id: string;
  statement: string;
  uncertainty: string;
  support: string[];
  counter: string[];
  /** Selected sources with no support or counter link; null when the answer's source list is unknown. */
  unevaluated: number | null;
  counterQueries: string[];
  /** Terminal of the counter-evidence search, or null when no search was recorded. */
  counterTerminal: string | null;
  tests: Record<string, unknown>[];
  /** What the observation was, as the result recorded it; "" for an older record without it. */
  observed: string;
  assumptions: string[];
  predicted: string[];
  missing: string[];
  status: string;
  /** What each distinguishing test expects if another explanation is right: the signals to drop this hypothesis. */
  refutationSignals: string[];
  /** The model's review of this hypothesis, or null when the result carries none. */
  review: ReviewView | null;
  /** What the generator said would make it give this hypothesis up (contract v3); an unconfirmed suggestion, empty for older results. */
  aiRefutation: string[];
};
export type Relation = "SUPPORT" | "COUNTER" | "NOT_APPLICABLE" | "UNEVALUATED";

const unique = (values: string[]) => Array.from(new Set(values));

/** Answer-selected evidence ids, or null when the result does not carry them. */
export function selectedEvidence(result: Record<string, unknown>): string[] | null {
  const refs = stringValues(result.selected_evidence_refs);
  return refs.length ? unique(refs) : null;
}

export function hypothesisRows(result: Record<string, unknown>): HypothesisRow[] {
  const selected = selectedEvidence(result);
  const reviews = reviewDecisions(result);
  return objectList(objectValue(result.portfolio).hypotheses).map(h => {
    // The server records support as evidence_refs; older results used support_evidence_refs.
    const support = unique([...stringValues(h.evidence_refs), ...stringValues(h.support_evidence_refs)]);
    const counter = unique(stringValues(h.counterevidence_refs));
    const review = objectValue(h.critical_review ?? objectValue(h.generation_details).critical_review);
    const id = textValue(h.hypothesis_id);
    const judged = reviews.get(id) ?? null;
    const tests = objectList(h.discriminating_tests);
    const row: HypothesisRow = {
      id, statement: textValue(h.statement), uncertainty: textValue(h.uncertainty), support, counter,
      unevaluated: null,
      counterQueries: stringValues(h.counterevidence_queries),
      counterTerminal: textValue(review.terminal) || null,
      tests, observed: textValue(h.observed_problem), assumptions: stringValues(h.assumptions), predicted: stringValues(h.predicted_observations),
      missing: stringValues(h.missing_evidence), status: textValue(h.status) || textValue(objectValue(h.generation_details).candidate_status),
      refutationSignals: tests.map(test => textValue(test.expected_if_alternative)).filter(Boolean), review: judged,
      aiRefutation: stringValues(h.refutation_conditions),
    };
    row.unevaluated = selected ? selected.filter(ref => relationOf(row, ref) === "UNEVALUATED").length : null;
    return row;
  });
}

/** One first-screen line about the counter-evidence search; "-" (not 0) while no search was recorded. */
export function counterSearchLine(result: Record<string, unknown>): string | null {
  const rows = hypothesisRows(result);
  if (!rows.length) return null;
  const searched = rows.filter(row => row.counterTerminal);
  if (!searched.length) return "반대 근거 탐색: 안 함 · 반박 -";
  return `반대 근거 탐색: ${searched.length}/${rows.length}개 가설 · 반박 ${searched.reduce((sum, row) => sum + row.counter.length, 0)}`;
}

/** A source the review cited for this hypothesis and judged "not supporting" is shown as not applicable; a source never looked at stays unevaluated. */
export function relationOf(row: HypothesisRow, ref: string): Relation {
  if (row.counter.includes(ref)) return "COUNTER";
  if (row.support.includes(ref)) return "SUPPORT";
  return row.review?.relation === "UNSUPPORTED" && row.review.evidenceRefs.includes(ref) ? "NOT_APPLICABLE" : "UNEVALUATED";
}
