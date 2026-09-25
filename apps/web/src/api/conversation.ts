import { z } from "zod";
import { rpc } from "./rpcClient";
import type { ExecutionError, ResearchFailure, ResultManifest } from "./research";
import { hasResearchContent } from "./presentation";
import type { Currentness } from "./historyModels";
import { ConversationRefreshError, isTransientReadFailure } from "./conversationRefresh";

const inputSchema = z.object({
  request_epoch: z.number().int(), request_revision_digest: z.string(),
  operation_id: z.string(), text: z.string(), edit_kind: z.string(), created_at: z.string(),
  authored_text_ref: z.object({ revision_digest: z.string() }).passthrough(),
});
const pageSchema = z.object({ turns: z.array(inputSchema), next_before_epoch: z.number().nullable(), history_limited: z.boolean() });
export type ConversationInput = z.infer<typeof inputSchema>;
export type ConversationTurn = { input: ConversationInput; result: Record<string, unknown> | null; state: string; unavailable?: string; error?:ExecutionError | null; failure?:ResearchFailure | null; terminalReason?:string | null; currentness?: Currentness };
export type ConversationPage = { turns: ConversationTurn[]; next: number | null; limited: boolean; supported: boolean; redacted?: boolean };

function redactedPage(): ConversationPage {
  return { turns: [], next: null, limited: false, supported: true, redacted: true };
}

export async function readConversation(projectId: string, threadId: string, before: number | null, signal?: AbortSignal, onBlocked?: () => void): Promise<ConversationPage> {
  try {
    return await readConversationPage(projectId, threadId, before, signal, onBlocked);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof ConversationRefreshError) throw error;
    if (isTransientReadFailure(error)) throw new ConversationRefreshError();
    // Replace the cached page, so a later transport failure cannot revive denied content.
    onBlocked?.();
    return redactedPage();
  }
}

async function readConversationPage(projectId: string, threadId: string, before: number | null, signal?: AbortSignal, onBlocked?: () => void): Promise<ConversationPage> {
  const response = await rpc<{ conversation?: unknown }>("thread/activity/list", {
    project_id: projectId, thread_id: threadId, ...(before === null ? {} : { conversation_before_epoch: before }),
  }, crypto.randomUUID(), signal);
  if (response.value.conversation === undefined) return { turns: [], next: null, limited: false, supported: false };
  const page = pageSchema.parse(response.value.conversation);
  const observations = await Promise.all(page.turns.map(async input => {
    const unavailable: ConversationTurn = { input, result: null, state: "UNAVAILABLE", unavailable: "이 답변은 현재 권한으로 읽을 수 없거나 저장 기준을 확인하지 못했습니다." };
    let value;
    try {
      ({ value } = await rpc<{ operation_id: string; state: string; result: Record<string, unknown> | null; error?:ExecutionError | null }>(
        "operation/result/read", { project_id: projectId, operation_id: input.operation_id }, crypto.randomUUID(), signal));
    } catch (error) {
      if (signal?.aborted) throw error;
      const transient = isTransientReadFailure(error);
      if (!transient) onBlocked?.();
      return { turn: unavailable, transient };
    }
    const result = value.result;
    if (value.operation_id !== input.operation_id || (result && (result.thread_id !== threadId || result.request_epoch !== input.request_epoch))) {
      onBlocked?.();
      return { turn: unavailable, transient: false };
    }
    return { turn: { input, result, state: value.state, error:value.error, failure:value.error?.data?.failure,
      terminalReason:typeof result?.terminal_reason === 'string'?result.terminal_reason:null } satisfies ConversationTurn, transient: false };
  }));
  if (observations.some(item => item.transient)) {
    // A concurrent denial/identity failure must take priority over retained cached content.
    if (observations.some(item => !item.transient && item.turn.state === "UNAVAILABLE")) {
      return redactedPage();
    }
    throw new ConversationRefreshError();
  }
  const turns = observations.map(item => item.turn);
  return { turns, next: page.next_before_epoch, limited: page.history_limited, supported: true };
}

/** A current checkpoint can update only the exact authored request it belongs to. */
type Checkpoint = Pick<ResultManifest, "request_ref" | "operation_id" | "result"> & Partial<Pick<ResultManifest,"terminal_reason"|"completion">>;
export function withCurrentCheckpoint(turns: ConversationTurn[], status?: {current_result?: Checkpoint | null; previous_result?: Checkpoint | null; operation_state?: string; operation_error?:ExecutionError | null; failure?:ResearchFailure | null; basis_currentness?: Currentness; request?: { operation_id: string }}): ConversationTurn[] {
  if (!status) return turns;
  const manifest = status.current_result ?? status.previous_result;
  const ownsOperation = !status.request || status.request.operation_id === manifest?.operation_id;
  return turns.map(turn => !turn.unavailable && turn.state !== 'UNAVAILABLE' && manifest?.request_ref?.revision_digest === turn.input.request_revision_digest &&
    manifest.operation_id === turn.input.operation_id ? {
      ...turn, result:hasResearchContent(manifest.result) ? manifest.result : turn.result,
      state:ownsOperation && ["FAILED", "CANCELLED"].includes(status.operation_state ?? "") ? status.operation_state! : status.current_result && ownsOperation ? status.operation_state ?? turn.state : ["FAILED", "CANCELLED"].includes(turn.state) ? turn.state : "STALE",
      error:(ownsOperation ? status.operation_error : null) ?? turn.error, failure:(ownsOperation ? status.failure : null) ?? turn.failure,
      currentness: status.basis_currentness ?? turn.currentness,
      terminalReason:manifest.terminal_reason ?? turn.terminalReason,
    } : turn);
}

const draftKey = (project: string, thread: string) => `thoth:draft:v1:${JSON.stringify([project, thread])}`;
export function readDraft(project: string, thread: string): string {
  try { return sessionStorage.getItem(draftKey(project, thread)) ?? ""; } catch { return ""; }
}
export function writeDraft(project: string, thread: string, text: string) {
  try { if (text) sessionStorage.setItem(draftKey(project, thread), text); else sessionStorage.removeItem(draftKey(project, thread)); } catch { /* Draft convenience only. */ }
}
