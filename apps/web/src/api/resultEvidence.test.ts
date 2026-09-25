import { afterEach, expect, it, vi } from "vitest";
import { readResultEvidence } from "./resultEvidence";
import { rpc, RpcError } from "./rpcClient";
import { evidenceResponse, selection, manifest, partial } from "./resultEvidenceTestFixture";

vi.mock("./rpcClient", async original => ({ ...await original<typeof import("./rpcClient")>(), rpc: vi.fn() }));
afterEach(() => vi.resetAllMocks());
function serve() {
  vi.mocked(rpc).mockImplementation(async (_method, args) => evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true));
}
it("reads 149 immutable selected refs in 50/50/49 pages without full-project reads", async () => {
  serve();
  const pages = [];
  let offset: number | null = 0;
  while (offset !== null) {
    const page = await readResultEvidence(selection, partial.result, { offset });
    pages.push(page); offset = page.next_offset;
  }
  expect(pages.map(page => page.items.length)).toEqual([50, 50, 49]);
  expect(pages.flatMap(page => page.items.map(item => item.span_id))).toEqual(manifest.source_refs);
  expect(pages.flatMap(page => page.items).filter(item => item.span).length).toBe(25);
  expect(vi.mocked(rpc).mock.calls.map(([method]) => method)).toEqual(Array(3).fill("thread/result/read"));
  for (const [, args] of vi.mocked(rpc).mock.calls) expect(args.result_revision_digest).toBe(selection.resultDigest);
});
it("resolves an old turn once through metadata and pins its immutable result before expansion", async () => {
  serve();
  const page = await readResultEvidence({ ...selection, resultDigest: undefined }, partial.result, { offset: 0 });
  expect(page.selected_count).toBe(149);
  const calls = vi.mocked(rpc).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[0][1].include_selected_evidence).toBeUndefined();
  expect(calls[1][1].result_revision_digest).toBe(manifest.result_revision_digest);
});
it.each(["project", "thread", "request", "operation", "result", "manifest", "changed-answer", "order", "count", "source-version", "text-hash", "bytes", "unavailable-bytes"])("rejects %s mismatches", async kind => {
  const response = evidenceResponse();
  const value = response.value, page = value.selected_evidence!;
  const item = page.items.find(item => item.span)!;
  if (kind === "project") value.project_id = "foreign";
  if (kind === "thread") value.thread_id = "foreign";
  if (kind === "request") value.request_revision_digest = "b".repeat(64);
  if (kind === "operation") value.operation_id = "foreign";
  if (kind === "result") value.result_revision_digest = "b".repeat(64);
  if (kind === "manifest") value.manifest.operation_id = "foreign";
  if (kind === "changed-answer") value.result = { answer: "new answer" };
  if (kind === "order") page.items.reverse();
  if (kind === "count") page.selected_count = 0;
  if (kind === "source-version") item.span!.source_version_id = "new-version";
  if (kind === "text-hash") item.span!.text_sha256 = "b".repeat(64);
  if (kind === "bytes") item.span!.exact_text += " changed";
  if (kind === "unavailable-bytes") item.availability = "UNAVAILABLE";
  vi.mocked(rpc).mockResolvedValue(response);
  await expect(readResultEvidence(selection, partial.result, { offset: 0 })).rejects.toThrow();
});
it("keeps unreadable and unknown-basis references instead of counting them as absent", async () => {
  const response = evidenceResponse();
  response.value.selected_evidence!.items.forEach(item => { item.availability = "UNKNOWN_BASIS"; item.span = null; });
  vi.mocked(rpc).mockResolvedValue(response);
  const page = await readResultEvidence(selection, partial.result, { offset: 0 });
  expect(page.selected_count).toBe(149); expect(page.items).toHaveLength(50);
  expect(page.items.every(item => item.span === null)).toBe(true);
});
it("rejects access denial without substituting another source read", async () => {
  vi.mocked(rpc).mockRejectedValue(new RpcError("denied", -32040, {}));
  await expect(readResultEvidence(selection, partial.result, { offset: 0 })).rejects.toThrow("denied");
  expect(rpc).toHaveBeenCalledTimes(1);
});
it("supports full results using the same exact manifest contract", async () => {
  const result = { ...partial.result, selected_evidence_refs: manifest.source_refs, portfolio: { hypotheses: [] }, action_plan: { alternatives: [] } };
  vi.mocked(rpc).mockResolvedValue(evidenceResponse(0, true, result));
  expect((await readResultEvidence(selection, result, { offset: 0 })).selected_count).toBe(149);
});
it("represents a confirmed empty manifest separately from missing metadata", async () => {
  const response = evidenceResponse();
  response.value.manifest.source_refs = [];
  response.value.selected_evidence = { ...response.value.selected_evidence!, selected_count: 0, items: [], next_offset: null };
  vi.mocked(rpc).mockResolvedValue(response);
  expect((await readResultEvidence(selection, partial.result, { offset: 0 })).selected_count).toBe(0);
  vi.mocked(rpc).mockResolvedValue({ ...response, value: { ...response.value, manifest: null } });
  await expect(readResultEvidence(selection, partial.result, { offset: 0 })).rejects.toThrow("저장 버전");
});
it("binds the historical manifest body without requiring operation-only envelope fields", async () => {
  const { contract_version: _contract, request_epoch: _epoch, thread_id: _thread, ...body } = partial.result;
  void _contract; void _epoch; void _thread;
  vi.mocked(rpc).mockResolvedValue(evidenceResponse(0, true, body));
  expect((await readResultEvidence(selection, body, { offset: 0 })).selected_count).toBe(149);
  expect((await readResultEvidence(selection, partial.result, { offset: 0 })).selected_count).toBe(149);
  await expect(readResultEvidence(selection, { ...partial.result, thread_id: "foreign" }, { offset: 0 })).rejects.toThrow();
  await expect(readResultEvidence(selection, { ...partial.result, request_epoch: 999 }, { offset: 0 })).rejects.toThrow();
});
