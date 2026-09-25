import { z } from "zod";
import { rpc, RpcError } from "./rpcClient";
import { capabilitySchema, changeGroupSchema, diffEntrySchema, digestSchema } from "./historySchemas";
import { mapComparison } from "./historyComparison";
import { restoreKey, type RestorePreview, type RestoreResult, type RestoreSelection } from "./historyModels";

export const restoreSelectionSchema = z.object({ project_id: z.string(), entity_type: z.string(), entity_id: z.string(),
  target_revision_digest: digestSchema, expected_current_head: digestSchema });
const impactSchema = z.object({ stale_refs: z.array(z.string()), invalidated_refs: z.array(z.string()), recalculate_refs: z.array(z.string()) });
const previewSchema = z.object({ contract_version: z.literal(2), selection: restoreSelectionSchema,
  profile_id: z.string().nullable(), availability: z.enum(["AVAILABLE", "BLOCKED", "NO_CHANGE"]),
  reason_codes: z.array(z.string()), capability: capabilitySchema, actor_scope_digest: digestSchema, basis_digest: digestSchema.nullable(),
  impact: impactSchema, diff: z.array(diffEntrySchema), summary_groups: z.array(changeGroupSchema), source_drift: z.array(z.string()),
  reanalysis: z.literal("NOT_REQUESTED"), external_effects: z.literal("PRESERVED") });
const applyResultSchema = z.object({ contract_version: z.literal(2), status: z.enum(["APPLIED", "NO_CHANGE"]),
  selection: restoreSelectionSchema, new_revision_digest: digestSchema.nullable(), new_revision_id: z.string().nullable(),
  receipt_id: z.string().nullable(), receipt_digest: digestSchema.nullable(), impact: impactSchema,
  reanalysis: z.literal("NOT_REQUESTED"), external_effects: z.literal("PRESERVED") });
export const restoreAttemptSchema = z.object({
  key: z.string().min(1), principalScope: digestSchema,
  input: z.object({ project_id: z.string(), selection: restoreSelectionSchema, contract_version: z.literal(2),
    preview_basis_digest: digestSchema, reason: z.string().min(1).max(2000) }),
});
export type RestoreAttempt = z.infer<typeof restoreAttemptSchema>;
export type RestoreOutcome = { state: "RUNNING"; operationId: string } | { state: "COMPLETE"; result: RestoreResult };
export function wireRestore(selection: RestoreSelection) {
  return { project_id: selection.projectId, entity_type: selection.entityType, entity_id: selection.entityId,
    target_revision_digest: selection.targetDigest, expected_current_head: selection.expectedHead };
}
function selectionOf(value: z.infer<typeof restoreSelectionSchema>): RestoreSelection {
  return { projectId: value.project_id, entityType: value.entity_type, entityId: value.entity_id,
    targetDigest: value.target_revision_digest, expectedHead: value.expected_current_head };
}
function requireSelection(actual: RestoreSelection, expected: RestoreSelection) {
  if (restoreKey(actual) !== restoreKey(expected)) throw new Error("복원 대상의 연결을 확인하지 못했습니다.");
}
export async function readRestorePreview(selection: RestoreSelection, signal?: AbortSignal): Promise<RestorePreview> {
  const response = await rpc<unknown>("revision/restore/preview", { project_id: selection.projectId, selection: wireRestore(selection), contract_version: 2 }, crypto.randomUUID(), signal);
  if (response.state !== "SUCCEEDED") throw new Error("복원 미리보기가 완료되지 않았습니다.");
  const value = previewSchema.parse(response.value);
  requireSelection(selectionOf(value.selection), selection);
  const states = { stale_refs: "재검토 필요", invalidated_refs: "현재 적용 불가", recalculate_refs: "다시 검토할 항목" };
  return { selection, basisDigest: value.basis_digest ?? "", principalScope: value.actor_scope_digest,
    availability: value.availability, applyReady: value.capability.restore === "RESTORE_SUPPORTED" && value.capability.apply_ready,
    profile: value.profile_id ?? "", reasons: [...new Set([...value.reason_codes, ...value.capability.reason_codes])],
    comparison: mapComparison(value.diff, value.summary_groups),
    impacts: Object.entries(value.impact).flatMap(([key, refs]) => refs.map(label => ({ label, state: states[key as keyof typeof states] }))),
    sourceChanges: value.source_drift, technical: value };
}
export function captureRestoreAttempt(preview: RestorePreview): RestoreAttempt {
  if (!preview.applyReady || preview.availability !== "AVAILABLE" || !preview.basisDigest || !preview.principalScope) {
    throw new Error("현재 복원을 적용할 수 없습니다. 새 미리보기를 확인하세요.");
  }
  return restoreAttemptSchema.parse({ key: crypto.randomUUID(), principalScope: preview.principalScope,
    input: { project_id: preview.selection.projectId, selection: wireRestore(preview.selection), contract_version: 2,
      preview_basis_digest: preview.basisDigest, reason: "연구 이력에서 확인한 과거 내용을 새 버전으로 적용" } });
}
function resultOf(value: unknown, attempt: RestoreAttempt, operationId: string): RestoreResult {
  const parsed = applyResultSchema.parse(value);
  const selection = selectionOf(parsed.selection);
  requireSelection(selection, selectionOf(attempt.input.selection));
  if (parsed.status === "APPLIED" && (!parsed.new_revision_digest || !parsed.new_revision_id || !parsed.receipt_id || !parsed.receipt_digest)) {
    throw new Error("복원 결과의 새 버전과 저장 기록을 확인하지 못했습니다.");
  }
  return { disposition: parsed.status, selection, newDigest: parsed.new_revision_digest, operationId,
    currentness: { state: parsed.status === "APPLIED" ? "REVIEW_REQUIRED" : "UNKNOWN_BASIS", reasons: [] }, technical: parsed };
}
export async function sendRestoreAttempt(attempt: RestoreAttempt, signal?: AbortSignal): Promise<RestoreOutcome> {
  const response = await rpc<unknown>("revision/restore/apply", attempt.input, attempt.key, signal);
  if (response.state === "RUNNING" && response.operation_id) return { state: "RUNNING", operationId: response.operation_id };
  if (response.state !== "SUCCEEDED") throw new Error("적용 완료를 확인하지 못했습니다. 원 요청의 결과를 확인하세요.");
  return { state: "COMPLETE", result: resultOf(response.value, attempt, response.operation_id) };
}
export async function readRestoreOperation(attempt: RestoreAttempt, operationId: string, signal?: AbortSignal): Promise<RestoreOutcome> {
  const response = await rpc<unknown>("operation/result/read", { project_id: attempt.input.project_id, operation_id: operationId }, crypto.randomUUID(), signal);
  const value = z.object({ operation_id: z.string(), state: z.string(), result: z.unknown(), error: z.object({ code: z.number().optional(), message: z.string().optional(), data: z.record(z.unknown()).optional() }).nullable().optional() }).parse(response.value);
  if (value.operation_id !== operationId) throw new Error("원 복원 요청의 연결을 확인하지 못했습니다.");
  if (value.state === "RUNNING") return { state: "RUNNING", operationId };
  if (value.state === "FAILED" || value.state === "CANCELLED") throw new RpcError(value.error?.message ?? "복원 요청이 적용되지 않았습니다.", value.error?.code ?? -32000, value.error?.data ?? {});
  if (value.state !== "SUCCEEDED") throw new Error("복원 요청의 상태를 확인하지 못했습니다.");
  return { state: "COMPLETE", result: resultOf(value.result, attempt, operationId) };
}
