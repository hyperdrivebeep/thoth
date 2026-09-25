// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { readHistoryEntry, readHistoryPage } from "./history";

const digest = "a".repeat(64);
const scope = { projectId: "project-1", threadId: "thread-1", requestDigest: digest };
const coverage = { visibility: "AUTHORIZED_SUBSET", association: "EXACT", scan: "COMPLETE_PAGE", reasons: [] };
const envelope = (value: unknown) => new Response(JSON.stringify({ jsonrpc: "2.0", id: "test", result: { operation_id: "", state: "SUCCEEDED", value } }), { status: 200 });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("preserves an empty limited page and its continuation cursor", async () => {
  const fetcher = vi.fn().mockResolvedValue(envelope({ contract_version: 2, items: [], next_cursor: "next-scanned-page", actor_scope_digest: digest,
    capability: { restore: "READ_ONLY", apply_ready: false, reason_codes: [] }, coverage: { ...coverage, scan: "LIMITED" } }));
  vi.stubGlobal("fetch", fetcher);
  const page = await readHistoryPage(scope, null);
  expect(page.items).toEqual([]);
  expect(page.nextCursor).toBe("next-scanned-page");
  expect(page.coverage.scan).toBe("LIMITED");
  expect(fetcher.mock.calls[0][0]).toBe("/rpc/query");
});

it("never falls back to a journaling RPC when a readonly method is unsupported", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ jsonrpc: "2.0", id: "test", error: { code: -32601, message: "Method not found", data: {} } })));
  vi.stubGlobal("fetch", fetcher);
  await expect(readHistoryPage(scope, null)).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/rpc/query");
});

it("rejects an answer belonging to a different request instead of displaying it", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(envelope({ contract_version: 2, project_id: "project-1", thread_id: "thread-1",
    request_revision_digest: "b".repeat(64), operation_id: "op-1", request: {}, manifest: null,
    result: { answer: "This is another request's answer" }, error: null, availability: "AVAILABLE", coverage,
    basis_currentness: { state: "CURRENT", reasons: [], execution_eligible: false } })));
  await expect(readHistoryEntry({ kind: "result", scope, operationId: "op-1" })).rejects.toThrow(/연결/);
});

it("returns an unavailable historical answer without substituting current content", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(envelope({ contract_version: 2, project_id: "project-1", thread_id: "thread-1",
    request_revision_digest: digest, operation_id: "op-1", request: {}, manifest: null, result: null,
    error: null, availability: "UNAVAILABLE", coverage: { ...coverage, association: "PARTIAL" },
    basis_currentness: { state: "UNAVAILABLE", reasons: ["ACCESS_REVOKED"], execution_eligible: false } })));
  const entry = await readHistoryEntry({ kind: "result", scope, operationId: "op-1" });
  expect(entry.result).toBeNull();
  expect(entry.availability).toBe("UNAVAILABLE");
  expect(entry.restoreSelection).toBeNull();
});
