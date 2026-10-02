import { objectList, objectValue, stringValues, textValue } from "../api/presentation";

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
};
export type Relation = "SUPPORT" | "COUNTER" | "UNEVALUATED";

const unique = (values: string[]) => Array.from(new Set(values));

/** Answer-selected evidence ids, or null when the result does not carry them. */
export function selectedEvidence(result: Record<string, unknown>): string[] | null {
  const refs = stringValues(result.selected_evidence_refs);
  return refs.length ? unique(refs) : null;
}

export function hypothesisRows(result: Record<string, unknown>): HypothesisRow[] {
  const selected = selectedEvidence(result);
  return objectList(objectValue(result.portfolio).hypotheses).map(h => {
    // The server records support as evidence_refs; older results used support_evidence_refs.
    const support = unique([...stringValues(h.evidence_refs), ...stringValues(h.support_evidence_refs)]);
    const counter = unique(stringValues(h.counterevidence_refs));
    const review = objectValue(h.critical_review ?? objectValue(h.generation_details).critical_review);
    const linked = new Set([...support, ...counter]);
    return {
      id: textValue(h.hypothesis_id), statement: textValue(h.statement), uncertainty: textValue(h.uncertainty), support, counter,
      unevaluated: selected ? selected.filter(ref => !linked.has(ref)).length : null,
      counterQueries: stringValues(h.counterevidence_queries),
      counterTerminal: textValue(review.terminal) || null,
      tests: objectList(h.discriminating_tests),
    };
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

export function relationOf(row: HypothesisRow, ref: string): Relation {
  if (row.counter.includes(ref)) return "COUNTER";
  return row.support.includes(ref) ? "SUPPORT" : "UNEVALUATED";
}
