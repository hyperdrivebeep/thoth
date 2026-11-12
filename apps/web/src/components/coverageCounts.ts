import type { CoverageMatrix, CoverageMatrixRow } from "../api/researchFollowup";
import { objectValue } from "../api/presentation";

/**
 * The one place that counts answer checks. The header tag, the conclusion lines and the checks table all read these
 * numbers, so they cannot disagree. An unavailable coverage read counts as no rows, never as zero fulfilled.
 */
export type CoverageCounts = {
  rows: CoverageMatrixRow[];
  total: number;
  satisfied: number;
  needsCheck: number;
};

export function coverageCounts(coverage?: CoverageMatrix | null): CoverageCounts {
  const rows = coverage && coverage.availability !== "UNAVAILABLE" ? coverage.rows : [];
  return {
    rows,
    total: rows.length,
    satisfied: rows.filter((row) => row.status === "SATISFIED").length,
    needsCheck: rows.filter((row) => row.status === "UNRESOLVED" || row.status === "NOT_ASSESSED")
      .length,
  };
}

/** How many sources the answer selected, from the stored count or the stored refs; undefined when neither is recorded. */
export function selectedEvidenceCount(result: Record<string, unknown>): number | undefined {
  const direct = result.selected_evidence_count ?? objectValue(result.retrieval).selected_count;
  if (typeof direct === "number" && direct > 0) return direct;
  return Array.isArray(result.selected_evidence_refs) && result.selected_evidence_refs.length
    ? result.selected_evidence_refs.length
    : undefined;
}
