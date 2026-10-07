import { rpc } from "./rpcClient";

export type TraceItem = { item_id: string; item_key: string; kind: string; title: string; fields: Record<string, string>; source_span_refs: string[] };
export type TraceLink = { link_id: string; from_id: string; to_id: string; relation: string };
export type TraceRule = {
  rule_id: string; rule_revision: number; criterion_id: string; measure: string; comparator: string; threshold: string;
  unit: string; condition: string; rounding: { digits: number; mode: string }; required: boolean;
};
export type TraceResult = {
  result_id: string; result_revision: number; criterion_id: string; condition: string; value: string | null; raw_value: string | null;
  unit: string; numerator: number | null; denominator: number | null; observed_at: string; source_span_refs: string[];
};
export type ChangedDependency = {
  kind: string; ref_id: string; field: string; before: string | null; after: string | null;
  before_revision: number | null; after_revision: number | null; criterion_ids: string[]; requirement_ids: string[];
};
export type Confirmation = { verdict_revision_digest: string; actor_id: string; confirmed_at: string; rationale: string };
export type ResultRef = { result_id: string; result_revision: number };
export type VerdictRevision = {
  subject_kind: "CRITERION" | "REQUIREMENT"; subject_id: string; state: string;
  cause: { trigger: string; changed: ChangedDependency[] };
  basis: { input_digest: string; source_span_refs: string[]; evidence_text_preserved: boolean; child_verdict_digests: string[] };
  selection: { policy: { name: string; version: number }; candidates: ResultRef[]; chosen: ResultRef[]; excluded: (ResultRef & { reason: string })[] } | null;
  conditions: { required: boolean; rule_id: string | null; condition: string | null; unit: string | null; required_criteria: string[] };
  actor: { actor_id: string; computed_at: string };
  reasons: { computed: string[]; human: string[] };
  parent_digest: string | null; verdict_digest: string; revision_digest: string;
};
export type Verdict = VerdictRevision & {
  currentness: { state: "CURRENT" | "STALE_BASIS"; changed_dependencies: ChangedDependency[] };
  confirmations: Confirmation[];
  recent_history: VerdictRevision[]; // newest first, the current revision included
  history_total: number;
};
export type TraceView = {
  record_digest: string | null; set_digest: string; items: TraceItem[]; links: TraceLink[]; rules: TraceRule[]; results: TraceResult[];
  verdicts: Verdict[]; confirmations: Confirmation[]; pending_changes: ChangedDependency[];
};

export type ImportIssue = { code: string; row: number | null; ref: string | null; detail: string };
export type PlannedChange = { kind: string; id: string; row: number; fields: string[] };
export type ImportMode = "CREATE" | "UPDATE";
export type ImportPreview = {
  preview_id: string; input_sha256: string; mode: ImportMode; current_set_digest: string | null; applicable: boolean;
  changes: { added: PlannedChange[]; updated: PlannedChange[]; deleted: PlannedChange[]; unchanged: number };
  conflicts: ImportIssue[]; losses: string[]; spreadsheet_suspects: ImportIssue[];
};
export type VerdictChange = { subject_kind: string; subject_id: string; before: string | null; after: string };
export type ImportApplied = { applied: boolean; reason?: string; record_digest: string | null; verdict_changes: VerdictChange[]; trace?: TraceView };
export type TraceExport = { format: string; filename: string; csv_text: string; sha256: string; set_digest: string; loss_manifest: string[] };

export type HistoryPage = { subject_kind: string; subject_id: string; history_total: number; revisions: VerdictRevision[]; next_before_digest: string | null };
export const HISTORY_PAGE = 20;

export const traceKey = (projectId: string) => ["verification-trace", projectId] as const;

const call = async <T,>(method: string, input: Record<string, unknown>, signal?: AbortSignal) =>
  (await rpc<T>(method, input, crypto.randomUUID(), signal)).value;

export const readTrace = (projectId: string, signal?: AbortSignal) => call<TraceView>("trace/read", { project_id: projectId }, signal);
export const exportTrace = (projectId: string) => call<TraceExport>("trace/export", { project_id: projectId });
export const previewTraceImport = (projectId: string, mode: ImportMode, csvText: string) =>
  call<ImportPreview>("trace/importPreview", { project_id: projectId, mode, csv_text: csvText });
export const applyTraceImport = (projectId: string, mode: ImportMode, csvText: string, preview: ImportPreview) =>
  call<ImportApplied>("trace/importApply", { project_id: projectId, mode, csv_text: csvText, preview_id: preview.preview_id, input_sha256: preview.input_sha256 });
export const confirmTraceVerdict = (projectId: string, revisionDigest: string, rationale: string, expectedDigest: string | null) =>
  call<TraceView>("trace/confirm", { project_id: projectId, verdict_revision_digest: revisionDigest, rationale, expected_digest: expectedDigest });
export const readTraceHistory = (projectId: string, kind: "CRITERION" | "REQUIREMENT", subjectId: string, beforeDigest: string, limit = HISTORY_PAGE) =>
  call<HistoryPage>("trace/history", { project_id: projectId, subject_kind: kind, subject_id: subjectId, before_digest: beforeDigest, limit });
