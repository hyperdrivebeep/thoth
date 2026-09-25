import { z } from "zod";
import { rpc } from "./rpcClient";
import { cutoffStates } from "./sourcePolicy";
import type { QueryClient } from "@tanstack/react-query";

export const sourceTimeAssessmentSchema = z.object({
  project_id: z.string(),
  artifact_id: z.string(),
  source_version_id: z.string(),
  byte_sha256: z.string(),
  cutoff_at: z.string(),
  cutoff_state: z.enum(cutoffStates),
  mode: z.enum(["AUTO","USER_CONFIRMATION","ADVANCED_CORRECTION"]),
  basis_observation_ids: z.array(z.string()),
  reason_code: z.string(),
  revision: z.number(),
  assessment_digest: z.string(),
  assessed_at: z.string(),
});
export type SourceTimeAssessment = z.infer<typeof sourceTimeAssessmentSchema>;

export const sourceTimeMutationBasisSchema = z.object({
  project_id: z.string(),
  artifact_id: z.string(),
  source_version_id: z.string(),
  byte_sha256: z.string(),
  expected_project_revision: z.number(),
  expected_cutoff_at: z.string(),
  expected_assessment_revision: z.number(),
  expected_metadata_digest: z.string(),
});

export function cutoffLabel(state: string) {
  if (state === "ELIGIBLE") return "연결됨 · 기준시점에 적합";
  if (state === "AFTER_CUTOFF") return "연결됨 · 기준시점보다 이후라 판단에는 사용하지 않음";
  if (state === "UNKNOWN_TIME") return "연결됨 · 인용할 때 시점을 확인";
  if (state === "PROHIBITED_CONTEXT") return "연결됨 · 사용 금지 상태";
  return state;
}

export async function confirmSourceTime(input: z.infer<typeof sourceTimeMutationBasisSchema> & {assertion:"ON_OR_BEFORE_CUTOFF"|"AFTER_CUTOFF"}) {
  return rpc<{source_time: SourceTimeAssessment}>("project/source/time/confirm", input, crypto.randomUUID());
}

export async function correctSourceTime(input: z.infer<typeof sourceTimeMutationBasisSchema> & {assertion?: "ON_OR_BEFORE_CUTOFF"|"AFTER_CUTOFF"; revert_unknown?: boolean; correction_reason: string}) {
  return rpc<{source_time: SourceTimeAssessment}>("project/source/time/correct", input, crypto.randomUUID());
}

/** Refresh query-backed views only; do not auto-refetch legacy journaling RPCs. */
export async function refreshSourceTimeViews(client: QueryClient, projectId: string) {
  await Promise.all(["sources", "evidence", "result-evidence", "research", "conversation", "research-history", "history-detail", "history-comparison", "restore-preview"]
    .map(prefix => client.invalidateQueries({ queryKey: [prefix, projectId] })));
}
