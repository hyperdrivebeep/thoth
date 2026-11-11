import { afterEach, describe, expect, it, vi } from "vitest";
import { readResultEvidence, type ResultSelection } from "./resultEvidence";
import { rpc } from "./rpcClient";
import fixture from "./fixtures/result-evidence-runtime.json";

/* Responses a real runtime made for a controlled (fake) model run, read as the web client reads them
 * (expected_result_contract v5). Regenerate with: python -m tests.integration.result_evidence_runtime
 * tests/integration/test_result_shown_view.py checks the file against the server's own functions. */
vi.mock("./rpcClient", async original => ({ ...await original<typeof import("./rpcClient")>(), rpc: vi.fn() }));
afterEach(() => vi.resetAllMocks());

type Wire = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Case = { selection: ResultSelection["scope"] & { operationId: string; resultDigest: string }; displayed: Wire; read: Wire; evidence_page: Wire };
const cases = fixture.cases as unknown as Record<"stored_current" | "stored_before_grounded_rows", Case>;
const selectionOf = (c: Case): ResultSelection => ({ kind: "result", scope: { projectId: c.selection.projectId, threadId: c.selection.threadId!, requestDigest: c.selection.requestDigest! },
  operationId: c.selection.operationId, resultDigest: c.selection.resultDigest });

/** Serves the stored responses; "edit" changes what the server says (a copy per call). */
function serve(c: Case, edit?: (value: Wire) => void) {
  vi.mocked(rpc).mockImplementation(async (_method, args) => {
    const value: Wire = structuredClone(args.include_selected_evidence === true ? { ...c.read, selected_evidence: c.evidence_page } : c.read);
    edit?.(value);
    return { operation_id: "", state: "SUCCEEDED", value };
  });
}
const BINDING = "연결을 확인하지 못했습니다";
const show = (c: Case, displayed: Wire = c.displayed) => readResultEvidence(selectionOf(c), displayed, { offset: 0 });

describe.each(["stored_current", "stored_before_grounded_rows"] as const)("the answer a real runtime stored, %s", name => {
  const c = cases[name];
  it("opens the evidence for the answer the screen shows", async () => {
    serve(c);
    const page = await show(c);
    expect(page.selected_count).toBeGreaterThan(0);
    expect(page.items.filter(item => item.availability === "AVAILABLE").length).toBe(page.items.length);
  });
  it("still rejects a different answer, a changed summary, a key the answer does not have, or a shown value left out of the result", async () => {
    serve(c);
    await expect(show(c, { ...c.displayed, answer: "another answer" })).rejects.toThrow(BINDING);
    await expect(show(c, { ...c.displayed, absence_assessments: { ...c.displayed.absence_assessments, not_checked_count: 99 } })).rejects.toThrow(BINDING);
    await expect(show(c, { ...c.displayed, invented_key: 1 })).rejects.toThrow(BINDING);
    serve(c, value => { value.shown_result = { ...value.shown_result, invented_key: 1 }; });
    await expect(show(c, { ...c.displayed, invented_key: 1 })).rejects.toThrow(BINDING);
  });
  it("still needs the result and the manifest to agree on every key the server did not declare changed", async () => {
    serve(c, value => { value.result = { ...value.result, answer: "another answer" }; value.shown_result = { ...value.shown_result, answer: "another answer" }; });
    await expect(show(c, { ...c.displayed, answer: "another answer" })).rejects.toThrow(BINDING);
  });
});

describe("a server that does not send shown_result or projected_keys (older)", () => {
  const old = (value: Wire) => { delete value.shown_result; delete value.projected_keys; };
  it("is read as before: the screen must hold keys of the stored result with the stored values", async () => {
    const c = cases.stored_current;
    serve(c, old);
    const stored = c.read.manifest.result as Wire;
    expect((await show(c, stored)).selected_count).toBeGreaterThan(0);
    // the summary view cannot be checked against the stored records without the server's help
    await expect(show(c)).rejects.toThrow(BINDING);
  });
  it("does not accept a read view that differs from the manifest", async () => {
    const c = cases.stored_before_grounded_rows;
    serve(c, old);
    await expect(show(c)).rejects.toThrow(BINDING);
  });
});
