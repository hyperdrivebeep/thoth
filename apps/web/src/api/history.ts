import { rpc } from "./rpcClient";
import { historyDetailSchema, historyPageSchema, historicalResultSchema, type WireHistoryItem } from "./historySchemas";
import type { HistoryEntry, HistoryKind, HistoryPage, HistoryRow, HistoryScope, HistorySelection, RecordReference } from "./historyModels";

export function wireScope(scope: HistoryScope) {
  return { project_id: scope.projectId, ...(scope.threadId ? { thread_id: scope.threadId } : {}),
    ...(scope.requestDigest ? { request_revision_digest: scope.requestDigest } : {}) };
}
export function wireRecord(record: RecordReference) {
  return { owner_kind: record.owner, project_id: record.projectId, immutable_id: record.id, revision_digest: record.digest };
}
async function query(method: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
  const response = await rpc<unknown>(method, input, crypto.randomUUID(), signal);
  if (response.state !== "SUCCEEDED") throw new Error("이력 조회가 완료되지 않았습니다.");
  return response.value;
}
function rowOf(item: WireHistoryItem, scope: HistoryScope): HistoryRow {
  if (item.record_ref.project_id !== scope.projectId) throw new Error("기록의 프로젝트 연결을 확인하지 못했습니다.");
  const record: RecordReference = { owner: item.record_ref.owner_kind, projectId: item.record_ref.project_id,
    id: item.record_ref.immutable_id, digest: item.record_ref.revision_digest,
    entityType: item.entity_type ?? undefined, entityId: item.entity_id ?? undefined };
  const origin = item.origin_request_revision_digest;
  const links = item.scope_links.filter(link => link.association === "PRODUCED_IN" && link.request_revision_digest === origin);
  const threads = new Set(links.map(link => link.thread_id));
  const thread = scope.threadId ? (threads.has(scope.threadId) ? scope.threadId : undefined)
    : (threads.size === 1 ? links[0].thread_id : undefined);
  const selection: HistorySelection = item.kind === "RESULT" && origin && thread
    ? { kind: "result", scope: { ...scope, threadId: thread, requestDigest: origin }, operationId: item.operation_id ?? undefined, resultDigest: item.record_ref.revision_digest, record,
      occurredAt: item.occurred_at, title: item.title }
    : { kind: "record", scope, record };
  return { id: item.item_id, kind: item.kind, title: item.title, occurredAt: item.occurred_at,
    association: item.association, membership: item.head_membership, availability: item.availability,
    currentness: item.currentness, selection, completion: item.completion, phase: item.phase };
}

export async function readHistoryPage(scope: HistoryScope, cursor: string | null, signal?: AbortSignal, kinds?: HistoryKind[]): Promise<HistoryPage> {
  const value = historyPageSchema.parse(await query("revision/timeline/read", {
    project_id: scope.projectId, scope: wireScope(scope), cursor, limit: 25, contract_version: 2, ...(kinds ? { kinds } : {}),
  }, signal));
  return { items: value.items.map(item => rowOf(item, scope)), nextCursor: value.next_cursor,
    coverage: value.coverage, actorScope: value.actor_scope_digest };
}

function questionOf(request: Record<string, unknown>): string | null {
  for (const key of ["authored_text", "effective_question", "text", "problem"]) {
    if (typeof request[key] === "string") return request[key];
  }
  return null;
}

export async function readHistoryEntry(selection: HistorySelection, signal?: AbortSignal): Promise<HistoryEntry> {
  if (selection.kind === "result") {
    const scope = selection.scope;
    const value = historicalResultSchema.parse(await query("thread/result/read", {
      project_id: scope.projectId, thread_id: scope.threadId, request_revision_digest: scope.requestDigest,
      ...(selection.resultDigest ? { result_revision_digest: selection.resultDigest } : {}), contract_version: 2,
    }, signal));
    if (value.project_id !== scope.projectId || value.thread_id !== scope.threadId || value.request_revision_digest !== scope.requestDigest
      || (selection.operationId && value.operation_id !== selection.operationId)
      || (selection.resultDigest && value.result_revision_digest !== selection.resultDigest)) {
      throw new Error("과거 답변의 요청·결과 연결을 확인하지 못했습니다.");
    }
    const readable = value.availability !== "UNAVAILABLE";
    // Comparing a result uses its observed immutable record identity, never a guessed ID.
    // A missing current-head projection must not hide an otherwise readable past answer.
    let comparisonEntry: HistoryEntry | null = null;
    if (readable && selection.record && selection.record.digest === value.result_revision_digest) {
      try { comparisonEntry = await readHistoryEntry({ kind: "record", scope, record: selection.record }, signal); }
      catch (error) { if (signal?.aborted) throw error; }
    }
    return { selection, title: selection.title ?? "당시 질문과 답변", occurredAt: selection.occurredAt ?? (typeof value.request.created_at === "string" ? value.request.created_at : null),
      kind: "RESULT", question: readable ? questionOf(value.request) : null, result: readable ? value.result : null, content: null,
      executionState: value.operation_state ?? null, resultPhase: typeof value.manifest?.completion === "string" ? value.manifest.completion : null,
      currentness: value.basis_currentness, availability: value.availability, reasons: value.coverage.reasons,
      currentHead: comparisonEntry?.currentHead ?? null, restoreSelection: comparisonEntry?.restoreSelection ?? null,
      capability: { restore: "READ_ONLY", applyReady: false, previewSupported: false, reasons: [] }, technical: readable ? value
        : { project_id: value.project_id, thread_id: value.thread_id, availability: value.availability, coverage: value.coverage, basis_currentness: value.basis_currentness } };
  }
  const value = historyDetailSchema.parse(await query("revision/timeline/item/read", {
    project_id: selection.scope.projectId, scope: wireScope(selection.scope), record_ref: wireRecord(selection.record), contract_version: 2,
  }, signal));
  const ref = value.item.record_ref;
  if (ref.project_id !== selection.scope.projectId || ref.project_id !== selection.record.projectId || ref.owner_kind !== selection.record.owner
    || ref.immutable_id !== selection.record.id || ref.revision_digest !== selection.record.digest) {
    throw new Error("선택한 기록의 연결을 확인하지 못했습니다.");
  }
  const { item } = value;
  const head = value.current_head_digest ?? null;
  const restoreSelection = ref.owner_kind === "SEMANTIC_REVISION" && item.entity_type && item.entity_id && head
    ? { projectId: ref.project_id, entityType: item.entity_type, entityId: item.entity_id, targetDigest: ref.revision_digest, expectedHead: head } : null;
  return { selection, title: item.title, occurredAt: item.occurred_at, kind: item.kind,
    question: item.kind === "REQUEST" && value.content ? questionOf(value.content) : null, result: null,
    content: item.availability === "UNAVAILABLE" ? null : value.content, currentness: item.currentness,
    availability: item.availability, reasons: value.coverage.reasons, currentHead: head, restoreSelection,
    capability: { restore: item.capability.restore, applyReady: item.capability.apply_ready, previewSupported: item.capability.preview_supported, reasons: item.capability.reason_codes },
    technical: item.availability === "UNAVAILABLE" ? { ...value, content: null } : value };
}
