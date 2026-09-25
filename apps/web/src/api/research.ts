import type { PackRunResult, WorkThread } from "../types";
import { z } from "zod";
import type { Currentness } from "./historyModels";
import type { CoverageMatrix, NextUserAction, UserProgressSummary } from "./researchFollowup";

export type ThreadAnalysis = PackRunResult["cycle"] & { selected_evidence_refs: string[] };
export type ResearchFailure = {
  primary: {reason_code:string; origin:string; detail?:string | null};
  secondary?: {reason_code:string; origin:string; detail?:string | null} | null;
  remote_observation?: string;
};
export type ExecutionError = {message?:string; data?:{reason_code?:string; failure?:ResearchFailure}};
export type ResultManifest = {
  request_ref?: { revision_digest: string };
  operation_id: string; phase: string; state: string; completion: string; terminal_reason: string | null;
  basis_digest: string; result: Record<string, unknown>; gaps: string[]; next_steps: string[];
  source_refs: string[]; record_refs: unknown[];
};
export type CompletedStage = {
  role?: string;
  state?: string;
  elapsed_ms?: number | null;
  context_bytes?: number | null;
  dispatch_ids?: string[];
};
export type ActivityEvent = {
  seq?: number;
  kind?: "note" | "action";
  key?: string | null;
  live?: boolean;
  action_type?: string;
  payload?: Record<string, unknown> | null;
};
export type ModelDispatch = {
  dispatch_id?: string;
  state?: string;
  received_bytes?: number | null;
  remote_stop?: string;
  transport_observation?: {
    elapsed_ms?: number | null;
    first_byte_ms?: number | null;
    first_response_ms?: number | null;
    received_bytes?: number | null;
    last_event_type?: string | null;
    local_cancel_requested?: boolean;
    http_status?: number | null;
  } | null;
};
export type UserActivityLocator = {
  page?: number | null;
  section?: string | null;
  cell?: string | null;
  line_start?: number | null;
  line_end?: number | null;
};
export type UserActivityTarget = {
  kind: "file" | "uri" | "artifact" | "span" | "table_cell" | "line_range" | "database_view" | "sandbox" | "model";
  title: string;
  display_ref?: string | null;
  host_alias?: string | null;
  safe_uri?: string | null;
  locator?: UserActivityLocator | null;
  source_version_id?: string | null;
  hash_short?: string | null;
  currentness?: "current" | "stale" | "unknown_time" | "access_unverified" | null;
  access_state: "allowed" | "blocked" | "out_of_scope" | "unknown";
};
export type UserActivityTool = {
  display_name: string;
  family: "LOCAL" | "GIT" | "MCP" | "POSTGRES" | "S3" | "SANDBOX" | "MODEL";
  operation: string;
  sanitized_args: Record<string, unknown>;
  raw_command_available: false;
  command_detail: "unavailable";
};
export type UserActivityResult = {
  summary_ko?: string | null;
  exit_code?: number | null;
  http_status?: number | null;
  duration_ms?: number | null;
  counts?: Record<string, number>;
  reason_code?: string | null;
};
export type UserActivityEvent = {
  schema_version: "thoth.user_activity_event.v1";
  event_id: string;
  seq: number;
  time?: string | null;
  severity: "info" | "success" | "warning" | "error" | "blocked" | "input_required";
  visibility: "default" | "expanded";
  announce: "none" | "polite" | "assertive";
  phase?: string | null;
  activity_kind: "tool" | "source" | "model" | "sandbox" | "connector" | "judgement" | "control";
  action: "search" | "open" | "fetch" | "parse" | "read" | "screen" | "select_candidate" | "adopt_support" | "adopt_counter" | "run" | "call_model" | "retry" | "cancel" | "hold" | "stage_complete";
  label_ko: string;
  why_ko?: string | null;
  state: "planned" | "running" | "succeeded" | "failed" | "blocked" | "cancel_requested" | "cancelled" | "timed_out" | "unknown_external_effect";
  research_relation: "none" | "discovered" | "opened" | "fetched" | "parsed" | "read" | "screened" | "selected_candidate" | "supports" | "contradicts" | "inconclusive" | "held" | "excluded" | "stale" | "access_blocked";
  target?: UserActivityTarget | null;
  tool?: UserActivityTool | null;
  result?: UserActivityResult | null;
  redaction?: {
    applied: boolean;
    classes?: string[];
    public_note_ko?: string | null;
    source_content_included: false;
  };
  refs?: {
    operation_alias?: string | null;
    receipt_ref?: string | null;
    source_ref?: string | null;
    revision_ref?: string | null;
  };
};
export type ResearchStatus = WorkThread & {
  usage?: { input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; state: string; unreported_calls: number; cumulative_token_limit_enforced: boolean; cached_input_tokens?: number | null };
  operation_state?: string; operation_error?: ExecutionError | null; request_epoch?: number;
  failure?: ResearchFailure | null;
  execution_summary?: {effective_execution_state:string;state_inconsistent:boolean;last_checkpoint_only:boolean;automatic_retry:boolean};
  freshness?: string; current_result?: ResultManifest | null; previous_result?: ResultManifest | null;
  basis_currentness?: Currentness;
  user_progress_summary?: UserProgressSummary | null;
  coverage_matrix?: CoverageMatrix | null;
  next_user_action?: NextUserAction | null;
  request?: { operation_id: string; authored_text?: string; effective_question?: string; model_settings?: unknown; policy_digest?: string; cutoff_at?: string };
  completed_stages?: CompletedStage[];
  activity_events?: ActivityEvent[];
  user_activity_events?: UserActivityEvent[];
  model_dispatches?: ModelDispatch[];
  attempt?: { operation_id: string; phase: string; status: string; draft_progress?: unknown; external_effect_state?: string; remote_observation?: string;
    request_ref?: { revision_digest: string };
    checkpoint_ref?: { project_id: string; entity_type: string; entity_id: string; revision_digest: string } | null;
  } | null;
  budget?: { calls: number; max_calls: number | null; reserved_tokens: number; max_reserved_tokens: number | null; actual_tokens?: number | null; actual_cost?: string | null; max_seconds: number | null; started_at: string } | null;
  cleanup?: unknown; post_execution_learning_current?: unknown[]; inputs?: unknown[];
};
export type ResearchAdmission = { thread_id: string; operation_id: string; request_epoch: number };

const stringList=z.array(z.string());
const dimension=z.object({status:z.string(),reason:z.string()});
const analysisDisplaySchema=z.object({
  assessment:z.object({assessment_id:z.string(),derived_status:stringList,missing_items:stringList,
    scope_identity:dimension,criterion_authority:dimension,evidence_coverage:dimension,comparability:dimension}),
  portfolio:z.object({portfolio_id:z.string(),hypotheses:z.array(z.object({
    hypothesis_id:z.string(),statement:z.string(),primary_locus:z.string().nullable(),uncertainty:z.string(),
    support_evidence_refs:stringList,counterevidence_queries:stringList,predicted_observations:stringList,
    discriminating_tests:z.array(z.object({procedure_candidate:z.string(),expected_if_true:z.string(),expected_if_alternative:z.string(),risk_tier:z.string()})),
  }))}),
  action_plan:z.object({plan_id:z.string(),frontier:stringList,alternatives:z.array(z.object({
    action_id:z.string(),action_family:z.string(),specification:z.string(),expected_information_value:z.string(),
    risk_tier:z.string(),execution_authority:z.string(),reversibility:z.string(),state:z.string(),source_refs:stringList,missing_evidence:stringList,
  }))}),
  commit:z.object({disposition:z.string(),committed_revision_ids:stringList,receipt:z.object({
    receipt_id:z.string(),receipt_digest:z.string(),claim_scopes:stringList,integrity_state:z.string(),provenance_state:z.string(),semantic_truth_certified:z.boolean(),
  })}),
  selected_evidence_refs:stringList,
});

/** Validate only: no default values, coercion or mutation of partial server records. */
export function isThreadAnalysis(value: Record<string, unknown>): value is ThreadAnalysis & Record<string, unknown> {
  return analysisDisplaySchema.safeParse(value).success;
}

const contextKey = "thoth:web-context:v1";
export function readWorkspaceContext(): { projectId: string; threadId: string } {
  try {
    const value = JSON.parse(sessionStorage.getItem(contextKey) ?? "null");
    if (value?.version === 1 && typeof value.projectId === "string" && typeof value.threadId === "string") return value;
  } catch { /* Storage can be disabled; canonical data is always fetched from the server. */ }
  return { projectId: "", threadId: "" };
}
export function saveWorkspaceContext(projectId: string, threadId: string) {
  try { sessionStorage.setItem(contextKey, JSON.stringify({ version: 1, projectId, threadId })); } catch { /* Noncanonical convenience only. */ }
}
