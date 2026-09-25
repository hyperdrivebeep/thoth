import { z } from "zod";
import { rpc } from "./rpcClient";
import { digestSchema } from "./historySchemas";

const basisCurrentnessSchema = z.object({
  state: z.enum(["CURRENT", "REVIEW_REQUIRED", "INVALIDATED", "UNKNOWN_BASIS", "UNAVAILABLE"]),
  reasons: z.array(z.string()).optional().default([]),
  execution_eligible: z.boolean().optional().default(false),
});

const nextUserActionSchema = z.object({
  schema_version: z.literal("1.0.0").optional().default("1.0.0"),
  action_type: z.enum(["NONE", "REVIEW_GAPS", "REVIEW_CURRENTNESS", "OPEN_RESULT_DETAIL", "START_FOLLOWUP", "UNSUPPORTED"]),
  label: z.string(),
  reason_codes: z.array(z.string()).optional().default([]),
  requires_permission: z.boolean().optional().default(false),
  target: z.record(z.unknown()).nullable().optional().default(null),
  basis: z.record(z.unknown()).optional().default({}),
});

export const userProgressSummarySchema = z.object({
  schema_version: z.literal("1.0.0").optional().default("1.0.0"),
  request_revision_digest: digestSchema,
  result_revision_digest: digestSchema.nullable().optional().default(null),
  state: z.enum(["PENDING_OR_NOT_PRODUCED", "IN_PROGRESS", "COMPLETE", "NEEDS_REVIEW", "HOLD", "FAILED", "CANCELLED", "UNKNOWN"]),
  currentness: basisCurrentnessSchema,
  progress_items: z.array(z.string()).optional().default([]),
  recorded_checks: z.array(z.string()).optional().default([]),
  remaining_gaps: z.array(z.string()).optional().default([]),
  unknowns: z.array(z.string()).optional().default([]),
  next_user_action: nextUserActionSchema,
});

const coverageRowSchema = z.object({
  requirement_id: z.string(),
  target: z.string(),
  question: z.string(),
  status: z.enum(["SATISFIED", "UNRESOLVED", "NOT_APPLICABLE", "NOT_ASSESSED"]),
  applicability: z.string(),
  relation: z.string(),
  validation: z.string(),
  blocker: z.string(),
  review_refs: z.array(z.string()).optional().default([]),
  evidence_refs: z.array(z.string()).optional().default([]),
  reason_codes: z.array(z.string()).optional().default([]),
});

export const coverageMatrixSchema = z.object({
  schema_version: z.literal("1.0.0").optional().default("1.0.0"),
  request_revision_digest: digestSchema,
  requirement_set_revision_digest: digestSchema.nullable().optional().default(null),
  coverage_revision_digest: digestSchema.nullable().optional().default(null),
  availability: z.enum(["AVAILABLE", "PARTIAL", "UNAVAILABLE"]).optional().default("AVAILABLE"),
  reason_codes: z.array(z.string()).optional().default([]),
  rows: z.array(coverageRowSchema).optional().default([]),
  summary: z.object({
    satisfied: z.number().int().optional().default(0),
    unresolved: z.number().int().optional().default(0),
    not_applicable: z.number().int().optional().default(0),
    not_assessed: z.number().int().optional().default(0),
    hold_targets: z.array(z.string()).optional().default([]),
    reason_codes: z.array(z.string()).optional().default([]),
  }).optional().default({}),
});

const resultIdentitySchema = z.object({
  request_revision_digest: digestSchema,
  result_revision_digest: digestSchema,
});

const decisionDeltaSchema = z.object({
  schema_version: z.literal("1.0.0").optional().default("1.0.0"),
  contract_version: z.literal(2).optional().default(2),
  project_id: z.string(),
  thread_id: z.string(),
  before: resultIdentitySchema,
  after: resultIdentitySchema,
  state: z.enum(["CHANGED", "NO_CHANGE", "PARTIAL", "UNAVAILABLE"]),
  reason_state: z.enum(["RECORDED", "UNKNOWN_REASON"]),
  reason_codes: z.array(z.string()).optional().default([]),
  reason_refs: z.array(z.record(z.unknown())).optional().default([]),
  basis_currentness: z.record(basisCurrentnessSchema),
  groups: z.array(z.object({
    kind: z.enum(["CONTENT", "EVIDENCE", "CONDITION", "STATUS", "ACTION", "OTHER"]),
    trace_paths: z.array(z.string()),
    changes: z.array(z.object({
      path: z.string(),
      before_present: z.boolean(),
      after_present: z.boolean(),
      before: z.unknown().nullable().optional(),
      after: z.unknown().nullable().optional(),
    })),
  })).optional().default([]),
});

const projectReviewItemSchema = z.object({
  item_id: z.string(),
  project_id: z.string(),
  thread_id: z.string(),
  request_revision_digest: digestSchema,
  result_revision_digest: digestSchema.nullable().optional().default(null),
  title: z.string(),
  priority: z.enum(["HIGH", "MEDIUM", "LOW"]),
  reason_codes: z.array(z.string()),
  currentness: basisCurrentnessSchema,
  next_user_action: nextUserActionSchema,
});

const projectReviewListSchema = z.object({
  schema_version: z.literal("1.0.0").optional().default("1.0.0"),
  contract_version: z.literal(2).optional().default(2),
  project_id: z.string(),
  items: z.array(projectReviewItemSchema),
  next_cursor: z.string().nullable().optional().default(null),
  coverage: z.enum(["COMPLETE_PAGE", "CONTINUATION", "LIMITED"]),
  unread_supported: z.literal(false).optional().default(false),
  assignment_supported: z.literal(false).optional().default(false),
});

export type NextUserAction = z.infer<typeof nextUserActionSchema>;
export type UserProgressSummary = z.infer<typeof userProgressSummarySchema>;
export type CoverageMatrix = z.infer<typeof coverageMatrixSchema>;
export type CoverageMatrixRow = z.infer<typeof coverageRowSchema>;
export type DecisionDelta = z.infer<typeof decisionDeltaSchema>;
export type ResultIdentity = z.infer<typeof resultIdentitySchema>;
export type ProjectReviewList = z.infer<typeof projectReviewListSchema>;
export type ProjectReviewItem = z.infer<typeof projectReviewItemSchema>;

async function query(method: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
  const response = await rpc<unknown>(method, input, crypto.randomUUID(), signal);
  if (response.state !== "SUCCEEDED") throw new Error("검토 조회가 완료되지 않았습니다.");
  return response.value;
}

export async function readDecisionDelta(projectId: string, threadId: string, before: ResultIdentity, after: ResultIdentity, signal?: AbortSignal): Promise<DecisionDelta> {
  const value = decisionDeltaSchema.parse(await query("thread/result/compare/read", {
    project_id: projectId, thread_id: threadId, before, after, contract_version: 2,
  }, signal));
  if (value.project_id !== projectId || value.thread_id !== threadId ||
      value.before.request_revision_digest !== before.request_revision_digest || value.before.result_revision_digest !== before.result_revision_digest ||
      value.after.request_revision_digest !== after.request_revision_digest || value.after.result_revision_digest !== after.result_revision_digest) {
    throw new Error("비교 대상 답변의 연결을 확인하지 못했습니다.");
  }
  return value;
}

export async function readProjectReviewList(projectId: string, cursor: string | null, signal?: AbortSignal): Promise<ProjectReviewList> {
  const value = projectReviewListSchema.parse(await query("project/review/list", {
    project_id: projectId, cursor, limit: 20, contract_version: 2,
  }, signal));
  if (value.project_id !== projectId) throw new Error("검토 목록의 프로젝트 연결을 확인하지 못했습니다.");
  return value;
}
