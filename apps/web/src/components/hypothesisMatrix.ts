import { t } from "../i18n/locale";
import type { HypothesisRow, Relation } from "./hypothesisView";
import { relationOf } from "./hypothesisView";

export type MatrixLine = { ref: string; index: number; cells: Relation[]; splits: boolean };

/**
 * One line per selected source. A line "splits" the hypotheses when at least two of its cells carry different
 * evaluated relations (support, counter, not applicable); an unevaluated cell is not a relation, so it never splits.
 */
export function matrixLines(rows: HypothesisRow[], selected: string[]): MatrixLine[] {
  return selected.map((ref, index) => {
    const cells = rows.map(row => relationOf(row, ref));
    return { ref, index, cells, splits: new Set(cells.filter(cell => cell !== "UNEVALUATED")).size > 1 };
  });
}

/** One sentence under the table about counter-evidence, or null when the table has counter cells to speak for themselves. */
export function noCounterNote(rows: HypothesisRow[], lines: MatrixLine[]): string | null {
  if (lines.some(line => line.cells.includes("COUNTER"))) return null;
  return rows.some(row => row.counterTerminal)
    ? t("hypotheses.noRefutation")
    : t("hypotheses.counterUnsearched");
}
