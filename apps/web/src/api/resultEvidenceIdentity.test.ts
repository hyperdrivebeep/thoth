import { expect, it } from "vitest";
import { currentResultDigest } from "./resultEvidenceIdentity";
import fixture from "./fixtures/qa04-evidence-manifest.json";
import type { ResearchStatus } from "./research";

const scope = { projectId: fixture.project_id, threadId: fixture.thread_id, requestDigest: fixture.request_revision_digest };
function status(): ResearchStatus {
  return { project_id: fixture.project_id, thread_id: fixture.thread_id, cycle_id: "fixture-cycle", problem: "fixture", lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [], working_head_digest: "fixture-head",
    current_result: { operation_id: fixture.operation_id, request_ref: fixture.request_ref, result: {}, phase: "PARTIAL", completion: "TERMINAL", state: "CURRENT", terminal_reason: null, basis_digest: "fixture-basis", gaps: [], next_steps: [], source_refs: fixture.source_refs, record_refs: [] },
    attempt: { operation_id: fixture.operation_id, request_ref: fixture.request_ref, phase: "PARTIAL", status: "FAILED",
      checkpoint_ref: { project_id: fixture.project_id, entity_type: "DECISION_OBJECT", entity_id: `result:${fixture.thread_id}`, revision_digest: fixture.result_revision_digest } },
  };
}
it("pins the synthetic checkpoint's immutable result identity", () => {
  expect(currentResultDigest(status(), scope, fixture.operation_id)).toBe(fixture.result_revision_digest);
});
it.each(["project", "thread", "operation", "request", "checkpoint", "missing"])("never borrows an unrelated %s binding", kind => {
  const value = status();
  if (kind === "project") value.project_id = "other";
  if (kind === "thread") value.thread_id = "other";
  if (kind === "operation") value.attempt!.operation_id = "other";
  if (kind === "request") value.attempt!.request_ref = { revision_digest: "f".repeat(64) };
  if (kind === "checkpoint") value.attempt!.checkpoint_ref!.entity_id = "result:other";
  if (kind === "missing") value.attempt!.checkpoint_ref = null;
  expect(currentResultDigest(value, scope, fixture.operation_id)).toBeUndefined();
});
