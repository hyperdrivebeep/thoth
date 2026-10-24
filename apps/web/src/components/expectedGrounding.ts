import { objectValue, textValue } from "../api/presentation";
import type { MessageKey } from "../i18n/messages";

const roleLabels = new Map<string, MessageKey>([
  ["OBSERVATION_RESTATEMENT", "grounding.observation"],
  ["EVIDENCE_INFERENCE", "grounding.inference"],
  ["HYPOTHESIS_ONLY", "grounding.hypothesis"],
  ["UNASSESSED", "grounding.unclassified"],
]);

export function expectedGrounding(row: Record<string, unknown>) {
  const grounding = objectValue(row.grounding);
  const known = grounding.schema_version === "1.0.0";
  const legacy = row.grounding_projection === "LEGACY_UNASSESSED";
  const fallback: MessageKey = "grounding.unclassified";
  return {
    role: known && !legacy ? roleLabels.get(textValue(grounding.derivation_role)) ?? fallback : undefined,
    rationale: known && !legacy ? textValue(grounding.rationale) : "",
    unassessed: known || legacy,
    unavailable: row.binding_availability !== undefined && row.binding_availability !== "AVAILABLE",
  };
}
