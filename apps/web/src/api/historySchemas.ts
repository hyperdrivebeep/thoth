import { z } from "zod";

export const digestSchema = z.string().regex(/^[a-f0-9]{64}$/i);
export const contentSchema = z.record(z.unknown());
export const currentnessSchema = z.object({
  state: z.enum(["CURRENT", "REVIEW_REQUIRED", "INVALIDATED", "UNKNOWN_BASIS", "UNAVAILABLE"]),
  reasons: z.array(z.string()), execution_eligible: z.boolean(),
});
export const capabilitySchema = z.object({
  restore: z.enum(["RESTORE_SUPPORTED", "READ_ONLY", "UNSUPPORTED"]),
  apply_ready: z.boolean(), preview_supported: z.boolean().optional().default(false), reason_codes: z.array(z.string()),
});
export const coverageSchema = z.object({
  visibility: z.literal("AUTHORIZED_SUBSET"), association: z.enum(["EXACT", "PARTIAL", "UNRESOLVED"]),
  scan: z.enum(["COMPLETE_PAGE", "CONTINUATION", "LIMITED"]), reasons: z.array(z.string()),
});
export const recordRefSchema = z.object({
  owner_kind: z.enum(["SEMANTIC_REVISION", "FULL_MEMORY"]), project_id: z.string(),
  immutable_id: z.string(), revision_digest: digestSchema,
});
export const historyItemSchema = z.object({
  item_id: z.string(), kind: z.enum(["REQUEST", "RESULT", "REVISION", "RESTORE", "MEMORY"]),
  occurred_at: z.string().datetime({ offset: true }), record_ref: recordRefSchema,
  operation_id: z.string().nullable().optional(), origin_request_revision_digest: digestSchema.nullable().optional(),
  scope_links: z.array(z.object({ thread_id: z.string(), request_revision_digest: digestSchema,
    association: z.enum(["PRODUCED_IN", "USED_BY", "RELATED_OBJECT"]) })),
  association: z.enum(["PRODUCED_IN", "USED_BY", "RELATED_OBJECT", "UNATTRIBUTED"]),
  head_membership: z.enum(["CURRENT", "ANCESTOR", "BRANCH", "UNKNOWN"]), title: z.string(),
  availability: z.enum(["AVAILABLE", "PARTIAL", "UNAVAILABLE"]),
  entity_type: z.string().nullable().optional(), entity_id: z.string().nullable().optional(),
  schema_family: z.string().nullable().optional(), currentness: currentnessSchema, capability: capabilitySchema,
  completion: z.enum(["CHECKPOINT", "TERMINAL"]).nullable().optional(), phase: z.string().nullable().optional(),
});
export const historyPageSchema = z.object({
  contract_version: z.literal(2), items: z.array(historyItemSchema), coverage: coverageSchema,
  next_cursor: z.string().nullable(), actor_scope_digest: digestSchema, capability: capabilitySchema,
});
export const historyDetailSchema = z.object({
  contract_version: z.literal(2), item: historyItemSchema, content: contentSchema.nullable(), coverage: coverageSchema,
  current_head_digest: digestSchema.nullable().optional(),
});
export const historicalResultSchema = z.object({
  contract_version: z.literal(2), project_id: z.string(), thread_id: z.string(), request_revision_digest: digestSchema,
  operation_id: z.string(), result_revision_digest: digestSchema.nullable().optional(), request: contentSchema,
  manifest: contentSchema.nullable(), result: contentSchema.nullable(), error: contentSchema.nullable(),
  operation_state: z.string().nullable().optional(), availability: z.enum(["AVAILABLE", "PARTIAL", "UNAVAILABLE"]),
  basis_currentness: currentnessSchema, coverage: coverageSchema,
});
export const diffEntrySchema = z.object({
  path: z.string(), before_present: z.boolean(), after_present: z.boolean(), before: z.unknown(), after: z.unknown(),
});
export const changeGroupSchema = z.object({
  kind: z.enum(["CONTENT", "EVIDENCE", "CONDITION", "STATUS", "IMPACT", "OTHER"]),
  trace_paths: z.array(z.string()), changes: z.array(diffEntrySchema), summary: z.string(),
});
export type WireHistoryItem = z.infer<typeof historyItemSchema>;
export type WireChange = z.infer<typeof diffEntrySchema>;
export type WireChangeGroup = z.infer<typeof changeGroupSchema>;
