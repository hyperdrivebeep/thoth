// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { captureRestoreAttempt, readRestorePreview, sendRestoreAttempt } from "./restore";
import type { RestorePreview, RestoreSelection } from "./historyModels";
const a = "a".repeat(64), b = "b".repeat(64);
const selection: RestoreSelection = { projectId: "p", entityType: "HYPOTHESIS", entityId: "h", targetDigest: a, expectedHead: b };
const preview: RestorePreview = { selection, basisDigest: a, principalScope: b, availability: "AVAILABLE", applyReady: true,
  profile: "hypothesis.v1", reasons: [], comparison: { changes: [], coverage: "COMPLETE", reasons: [] }, impacts: [], sourceChanges: [], technical: {} };
const envelope = (state: string, value: unknown, operation_id = "op") => new Response(JSON.stringify({ jsonrpc: "2.0", id: "t", result: { state, value, operation_id } }));
afterEach(() => vi.unstubAllGlobals());

it("keeps the same key and payload after transport loss, and RUNNING is not APPLIED", async () => {
  const attempt = captureRestoreAttempt(preview);
  const fetcher = vi.fn().mockRejectedValueOnce(new TypeError("network lost")).mockResolvedValueOnce(envelope("RUNNING", {}));
  vi.stubGlobal("fetch", fetcher);
  await expect(sendRestoreAttempt(attempt)).rejects.toThrow("network lost");
  const second = await sendRestoreAttempt(attempt);
  expect(second.state).toBe("RUNNING");
  const inputs = fetcher.mock.calls.map(call => JSON.parse(call[1].body as string));
  expect(inputs[0].params).toEqual(inputs[1].params);
  expect(inputs[0].method).toBe("revision/restore/apply");
});

it("does not create an apply attempt while the server readiness gate is closed", () => {
  expect(() => captureRestoreAttempt({ ...preview, applyReady: false })).toThrow();
  expect(() => captureRestoreAttempt({ ...preview, availability: "BLOCKED" })).toThrow();
});

it("rejects a preview for another entity even when the request succeeded", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(envelope("SUCCEEDED", {
    contract_version: 2, selection: { project_id: "p", entity_type: "HYPOTHESIS", entity_id: "other", target_revision_digest: a, expected_current_head: b },
    profile_id: "hypothesis.v1", availability: "AVAILABLE", reason_codes: [], actor_scope_digest: b, basis_digest: a,
    capability: { restore: "RESTORE_SUPPORTED", apply_ready: true, reason_codes: [] },
    impact: { stale_refs: [], invalidated_refs: [], recalculate_refs: [] }, diff: [], summary_groups: [], source_drift: [],
    reanalysis: "NOT_REQUESTED", external_effects: "PRESERVED",
  })));
  await expect(readRestorePreview(selection)).rejects.toThrow(/연결/);
});
