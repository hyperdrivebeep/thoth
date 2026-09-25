import manifest from "./fixtures/qa04-evidence-manifest.json";
import partial from "./fixtures/qa04-partial-result.json";
import citations from "./fixtures/qa04-cited-spans.json";
import type { ResultSelection } from "./resultEvidence";

export { manifest, partial, citations };
export const selection: ResultSelection = { kind: "result", scope: partial.scope, operationId: partial.operationId, resultDigest: manifest.result_revision_digest };
// Fictional greenhouse text, identities, hashes and locators are synthetic; status badges below are test-only.
export function evidenceResponse(offset = 0, expand = true, result: Record<string, unknown> = partial.result) {
  return { operation_id: "", state: "SUCCEEDED", value: {
    contract_version: 2, project_id: manifest.project_id, thread_id: manifest.thread_id,
    request_revision_digest: manifest.request_revision_digest, operation_id: manifest.operation_id,
    result_revision_digest: manifest.result_revision_digest, request: { project_id: manifest.project_id, thread_id: manifest.thread_id,
      operation_id: manifest.operation_id, request_epoch: partial.result.request_epoch }, manifest: { ...manifest, result }, result,
    error: null, availability: "AVAILABLE", basis_currentness: { state: "CURRENT", reasons: [], execution_eligible: false },
    coverage: { visibility: "AUTHORIZED_SUBSET", association: "EXACT", scan: "COMPLETE_PAGE", reasons: [] },
    selected_evidence: expand ? { schema_version: "1.0.0", result_revision_digest: manifest.result_revision_digest,
      actor_scope_digest: "a".repeat(64), selected_count: manifest.source_refs.length, offset, limit: 50,
      next_offset: offset + 50 < manifest.source_refs.length ? offset + 50 : null,
      items: manifest.source_refs.slice(offset, offset + 50).map(span_id => {
        const span = citations.find(item => item.span_id === span_id);
        return { span_id, availability: span ? "AVAILABLE" : "UNAVAILABLE", reason_codes: span ? [] : ["TEST_FIXTURE_NOT_INCLUDED"],
          span: span ? { ...span, project_id: manifest.project_id, authority_state: "UNCLASSIFIED", cutoff_state: "ELIGIBLE", verification_state: "NOT_CHECKED", support_state: "EXTRACTED", extraction_method: "test-fixture", schema_version: "1.0.0" } : null };
      }),
    } : null,
  } };
}
