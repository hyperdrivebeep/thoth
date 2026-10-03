import type { CoverageMatrixRow } from "../api/researchFollowup";

/**
 * How a criterion's AI-reviewed evidence can be used. Computed only from fields the server already stores on the
 * criterion row; a combination no rule covers is counted as UNCLASSIFIED instead of being guessed.
 * This is a separate axis from "fulfilled": a criterion can have directly usable evidence and still be unresolved.
 */
export type EvidenceUse = "DIRECT" | "CONDITIONAL" | "UNUSABLE" | "INSUFFICIENT" | "UNCLASSIFIED";

export const evidenceUseOrder: EvidenceUse[] = ["DIRECT", "CONDITIONAL", "INSUFFICIENT", "UNUSABLE", "UNCLASSIFIED"];
export const evidenceUseLabel: Record<EvidenceUse, string> = {
  DIRECT: "직접 사용 가능", CONDITIONAL: "조건부·참고용", INSUFFICIENT: "자료 없음·부족", UNUSABLE: "직접 사용 불가", UNCLASSIFIED: "분류 안 됨",
};

export function classifyEvidenceUse(row: CoverageMatrixRow): EvidenceUse {
  if (row.relation === "IRRELEVANT" || row.applicability.startsWith("NOT_APPLICABLE") || row.validation === "REJECTED") return "UNUSABLE";
  if (row.relation === "INSUFFICIENT" || row.status === "NOT_ASSESSED") return "INSUFFICIENT";
  if ((row.relation === "SUPPORTS" || row.relation === "REFUTES") && row.applicability === "APPLICABLE" && row.validation === "APPLIED") return "DIRECT";
  if (row.relation === "QUALIFIES" || row.validation === "INCONCLUSIVE" || row.applicability === "UNKNOWN") return "CONDITIONAL";
  return "UNCLASSIFIED";
}

export function groupEvidenceUse(rows: CoverageMatrixRow[]): Record<EvidenceUse, CoverageMatrixRow[]> {
  const groups: Record<EvidenceUse, CoverageMatrixRow[]> = { DIRECT: [], CONDITIONAL: [], INSUFFICIENT: [], UNUSABLE: [], UNCLASSIFIED: [] };
  for (const row of rows) groups[classifyEvidenceUse(row)].push(row);
  return groups;
}

