// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { loadPendingRestore, savePendingRestore } from "./restorePending";
import type { RestoreAttempt } from "./restore";
const a = "a".repeat(64), b = "b".repeat(64), c = "c".repeat(64);
const scope = { projectId: "p", threadId: "t" };
const selection = { projectId: "p", entityType: "HYPOTHESIS", entityId: "h", targetDigest: a, expectedHead: b };
const attempt: RestoreAttempt = { key: "one-click", principalScope: c,
  input: { project_id: "p", selection: { project_id: "p", entity_type: "HYPOTHESIS", entity_id: "h", target_revision_digest: a, expected_current_head: b },
    contract_version: 2, preview_basis_digest: a, reason: "사용자 복원" } };
afterEach(() => sessionStorage.clear());
it("retains the original request when only the current head changes", () => {
  savePendingRestore(scope, selection, attempt, "operation-1");
  expect(loadPendingRestore(scope, { ...selection, expectedHead: c }, c)?.attempt).toEqual(attempt);
});
it("does not reuse a pending request in a different principal or session scope", () => {
  savePendingRestore(scope, selection, attempt, null);
  expect(loadPendingRestore(scope, selection, b)).toBeNull();
  expect(loadPendingRestore({ ...scope, threadId: "another-session" }, selection, c)).toBeNull();
  expect(loadPendingRestore(scope, selection, c)?.attempt.key).toBe("one-click");
});
