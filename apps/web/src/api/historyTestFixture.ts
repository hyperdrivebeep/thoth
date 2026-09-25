/** Explicit synthetic wire fixture, used only by tests and the DEV history example. */
export const historyFixtureIds = { project: "history-example", thread: "session-example", actor: "e".repeat(64),
  request1: "1".repeat(64), result1: "2".repeat(64), request2: "3".repeat(64), result2: "4".repeat(64),
  old: "a".repeat(64), current: "b".repeat(64), restored: "c".repeat(64), basis: "d".repeat(64) };
type FixtureInput = Record<string, unknown>;
type FixtureRequest = { method: string; id: string; params: { input: FixtureInput; _meta: { idempotencyKey: string } } };
export function createHistoryFixtureTransport(options: { applyReady?: boolean; loseApplyResponse?: boolean } = {}) {
  const ids = historyFixtureIds;
  const calls: { method: string; input: FixtureInput; key: string; url: string }[] = [];
  let head = ids.current;
  let lost = false;
  let appliedCount = 0;
  const completed = new Map<string, unknown>();
  const ready = options.applyReady ?? false;
  const capability = { restore: ready ? "RESTORE_SUPPORTED" : "READ_ONLY", preview_supported: true, apply_ready: ready, reason_codes: ready ? [] : ["RESTORE_APPLY_NOT_READY"] };
  const coverage = { visibility: "AUTHORIZED_SUBSET", association: "EXACT", scan: "COMPLETE_PAGE", reasons: [] };
  const currentness = (state = "CURRENT") => ({ state, reasons: [], execution_eligible: false });
  const titles: Record<string, string> = { [ids.old]: "연결 자료에서 초기 가설을 정리했습니다", [ids.current]: "추가 근거에 맞춰 가설의 적용 조건을 좁혔습니다", [ids.restored]: "과거 가설 내용을 새 버전으로 적용했습니다" };
  const content = (digest: string) => ({ statement: digest === ids.current
    ? "현재 연결 자료만으로 원인을 확정할 수 없어 추가 비교가 필요합니다."
    : "관측 조건의 차이가 결과에 영향을 주었을 가능성을 검토합니다.",
    uncertainty: "아직 독립적인 검증 결과가 없습니다.", counterevidence_queries: ["같은 조건에서도 차이가 유지되는가?"] });
  const ref = (digest: string) => ({ owner_kind: "SEMANTIC_REVISION", project_id: ids.project,
    immutable_id: `revision:${digest.slice(0, 8)}`, revision_digest: digest });
  const revisionItem = (digest: string, hour: string) => ({ item_id: `hypothesis:${digest}`, kind: digest === ids.restored ? "RESTORE" : "REVISION",
    occurred_at: `2026-09-21T${hour}:00+09:00`, record_ref: ref(digest), operation_id: null,
    origin_request_revision_digest: ids.request1, scope_links: [{ thread_id: ids.thread, request_revision_digest: ids.request1, association: "PRODUCED_IN" }],
    association: "PRODUCED_IN", head_membership: head === digest ? "CURRENT" : "ANCESTOR", title: titles[digest], availability: "AVAILABLE",
    entity_type: "HYPOTHESIS", entity_id: "hypothesis:example-1", schema_family: "HypothesisRecord",
    currentness: currentness(head === ids.restored ? "REVIEW_REQUIRED" : head === digest ? "CURRENT" : "REVIEW_REQUIRED"), capability });
  const resultItem = (second: boolean) => ({ ...revisionItem(second ? ids.current : ids.old, second ? "14:35" : "14:02"),
    item_id: second ? "result-2" : "result-1", kind: "RESULT", title: second ? "추가 질문에 대한 답변" : "첫 질문에 대한 답변",
    record_ref: ref(second ? ids.result2 : ids.result1), origin_request_revision_digest: second ? ids.request2 : ids.request1,
    operation_id: second ? "operation-2" : "operation-1", entity_type: "DECISION_OBJECT", entity_id: "result:example",
    scope_links: [{ thread_id: ids.thread, request_revision_digest: second ? ids.request2 : ids.request1, association: "PRODUCED_IN" }],
    currentness: currentness(head === ids.restored ? "REVIEW_REQUIRED" : "UNKNOWN_BASIS"),
    capability: { restore: "READ_ONLY", apply_ready: false, reason_codes: [] } });
  const diff = (before: string, after: string) => before === after ? [] : [{ path: "/statement", before_present: true, after_present: true, before, after }];
  const groups = (changes: ReturnType<typeof diff>) => changes.length ? [{ kind: "CONTENT", trace_paths: changes.map(item => item.path), changes, summary: "저장된 설명이 바뀌었습니다" }] : [];
  const answer = (second: boolean) => second ? "두 번째 답변: 측정 조건과 원문 근거를 먼저 비교하세요." : "첫 번째 답변: 관측 조건의 차이를 가설로 두고 반대 근거를 확인하세요.";
  const impact = { stale_refs: ["hypothesis:example-1"], invalidated_refs: [], recalculate_refs: ["action-plan:example", "result:example"] };
  async function handle(request: FixtureRequest): Promise<unknown> {
    const input = request.params.input;
    if (request.method === "revision/timeline/read") return { contract_version: 2, actor_scope_digest: ids.actor, capability, coverage, next_cursor: null,
      items: [...(head === ids.restored ? [revisionItem(head, "14:45")] : []), revisionItem(ids.current, "14:40"), resultItem(true), revisionItem(ids.old, "14:05"), resultItem(false)] };
    if (request.method === "project/review/list") return { schema_version: "1.0.0", contract_version: 2, project_id: ids.project, coverage: "COMPLETE_PAGE", next_cursor: null,
      unread_supported: false, assignment_supported: false, items: [
        { item_id: "review-result-2", project_id: ids.project, thread_id: ids.thread, request_revision_digest: ids.request2, result_revision_digest: ids.result2,
          title: "추가 질문에 대한 답변", priority: "HIGH", reason_codes: ["UNKNOWN_BASIS"], currentness: currentness("REVIEW_REQUIRED"),
          next_user_action: { schema_version: "1.0.0", action_type: "REVIEW_CURRENTNESS", label: "현재 자료 기준으로 다시 확인", reason_codes: ["UNKNOWN_BASIS"], requires_permission: false, target: null, basis: {} } },
      ] };
    if (request.method === "thread/result/compare/read") return { schema_version: "1.0.0", contract_version: 2,
      project_id: ids.project, thread_id: ids.thread, before: input.before, after: input.after, state: "CHANGED",
      reason_state: "UNKNOWN_REASON", reason_codes: [], reason_refs: [],
      basis_currentness: { before: currentness("UNKNOWN_BASIS"), after: currentness("REVIEW_REQUIRED") },
      groups: [{ kind: "CONTENT", trace_paths: ["/result/answer"], changes: [{ path: "/result/answer",
        before_present: true, after_present: true, before: answer(false), after: answer(true) }] }] };
    if (request.method === "revision/timeline/item/read") {
      const record = input.record_ref as { revision_digest: string };
      if ([ids.result1, ids.result2].includes(record.revision_digest)) return { contract_version: 2, item: resultItem(record.revision_digest === ids.result2),
        content: { answer: answer(record.revision_digest === ids.result2) }, current_head_digest: ids.result2, coverage };
      return { contract_version: 2, item: revisionItem(record.revision_digest, "14:05"), content: content(record.revision_digest), current_head_digest: head, coverage };
    }
    if (request.method === "thread/result/read") {
      const second = input.request_revision_digest === ids.request2;
      return { contract_version: 2, project_id: ids.project, thread_id: ids.thread,
        request_revision_digest: second ? ids.request2 : ids.request1, operation_id: second ? "operation-2" : "operation-1",
        result_revision_digest: second ? ids.result2 : ids.result1,
        request: { authored_text: second ? "조건을 같게 비교하려면 무엇을 확인해야 하나요?" : "결과가 달라진 이유를 어떻게 조사할까요?", created_at: "2026-09-21T14:00:00+09:00" },
        manifest: null, result: { answer: answer(second) },
        error: null, operation_state: "SUCCEEDED", availability: "AVAILABLE", basis_currentness: currentness(head === ids.restored ? "REVIEW_REQUIRED" : "UNKNOWN_BASIS"), coverage };
    }
    if (request.method === "revision/diff/read") {
      const resultDiff = [ids.result1, ids.result2].includes(String(input.from_revision_digest));
      const changes = resultDiff ? diff(answer(input.from_revision_digest === ids.result2), answer(input.to_revision_digest === ids.result2)).map(item => ({ ...item, path: "/result/answer" }))
        : diff(content(String(input.from_revision_digest)).statement, content(String(input.to_revision_digest)).statement);
      return { contract_version: 2, from_revision_digest: input.from_revision_digest, to_revision_digest: input.to_revision_digest, diff: changes, summary_groups: groups(changes), coverage };
    }
    if (request.method === "revision/restore/preview") {
      const selection = input.selection as FixtureInput;
      const changes = diff(content(head).statement, content(String(selection.target_revision_digest)).statement);
      return { contract_version: 2, selection, profile_id: "hypothesis.v1", availability: changes.length ? "AVAILABLE" : "NO_CHANGE",
        reason_codes: [], capability, actor_scope_digest: ids.actor, basis_digest: ids.basis, impact, diff: changes,
        summary_groups: groups(changes), source_drift: [], reanalysis: "NOT_REQUESTED", external_effects: "PRESERVED" };
    }
    if (request.method === "revision/restore/apply") {
      const key = request.params._meta.idempotencyKey;
      if (completed.has(key)) return completed.get(key);
      if (!ready) throw new Error("RESTORE_APPLY_NOT_READY");
      appliedCount += 1; head = ids.restored;
      const result = { contract_version: 2, status: "APPLIED", selection: input.selection, new_revision_digest: head,
        new_revision_id: "revision-restored", receipt_id: "receipt-example", receipt_digest: ids.basis,
        restore_audit_id: "audit-example", impact, reanalysis: "NOT_REQUESTED", external_effects: "PRESERVED" };
      completed.set(key, result);
      if (options.loseApplyResponse && !lost) { lost = true; throw new TypeError("검증용 응답 유실"); }
      return result;
    }
    if (request.method === "operation/result/read") return { operation_id: input.operation_id, state: "SUCCEEDED", result: [...completed.values()][0] ?? null, error: null };
    throw new Error(`Unsupported fixture method: ${request.method}`);
  }
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body)) as FixtureRequest;
    calls.push({ method: request.method, input: request.params.input, key: request.params._meta.idempotencyKey, url: String(url) });
    const value = await handle(request);
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: request.id,
      result: { operation_id: request.method === "revision/restore/apply" ? "operation-restore" : "", state: "SUCCEEDED", value } }));
  };
  return { fetch: fetcher, calls, get appliedCount() { return appliedCount; } };
}
