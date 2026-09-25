import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { createHistoryFixtureTransport, historyFixtureIds as ids } from "./historyTestFixture";

it("matches the actual Python request and response DTOs without creating a runtime", async () => {
  const fixture = createHistoryFixtureTransport({ applyReady: true });
  const selection = { project_id: ids.project, entity_type: "HYPOTHESIS", entity_id: "hypothesis:example-1",
    target_revision_digest: ids.old, expected_current_head: ids.current };
  const scope = { project_id: ids.project, thread_id: ids.thread };
  const cases = [
    { method: "revision/timeline/read", input: { project_id: ids.project, scope, cursor: null, limit: 25, contract_version: 2 } },
    { method: "revision/timeline/item/read", input: { project_id: ids.project, scope, contract_version: 2,
      record_ref: { owner_kind: "SEMANTIC_REVISION", project_id: ids.project, immutable_id: "revision:aaaaaaaa", revision_digest: ids.old } } },
    { method: "thread/result/read", input: { project_id: ids.project, thread_id: ids.thread, request_revision_digest: ids.request1, contract_version: 2 } },
    { method: "revision/diff/read", input: { project_id: ids.project, from_revision_digest: ids.old, to_revision_digest: ids.current, contract_version: 2 } },
    { method: "revision/restore/preview", input: { project_id: ids.project, selection, contract_version: 2 } },
    { method: "revision/restore/apply", input: { project_id: ids.project, selection, preview_basis_digest: ids.basis, reason: "contract fixture", contract_version: 2 } },
  ];
  const captured = [];
  for (const item of cases) {
    const response = await fixture.fetch("/fixture-only", { method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: "fixture", method: item.method,
      params: { input: item.input, _meta: { idempotencyKey: item.method } } }) });
    captured.push({ ...item, value: (await response.json()).result.value });
  }
  const repository = fileURLToPath(new URL("../../../../", import.meta.url));
  const python = [join(repository, ".venv", "Scripts", "python.exe"), join(repository, ".venv", "bin", "python")].find(existsSync);
  if (!python) throw new Error("Web wire contract validation requires the repository Python environment.");
  const source = [
    "import json, sys",
    "from thoth.domain.research_history import HistoryTimelineInput, HistoryPage, HistoryItemInput, HistoryDetail, HistoricalResultInput, HistoricalResultView, HistoryDiffInput, HistoryDiff",
    "from thoth.domain.restore import RestorePreviewInput, RestorePreview, RestoreApplyInput, RestoreApplyResult",
    "models={'revision/timeline/read':(HistoryTimelineInput,HistoryPage),'revision/timeline/item/read':(HistoryItemInput,HistoryDetail),'thread/result/read':(HistoricalResultInput,HistoricalResultView),'revision/diff/read':(HistoryDiffInput,HistoryDiff),'revision/restore/preview':(RestorePreviewInput,RestorePreview),'revision/restore/apply':(RestoreApplyInput,RestoreApplyResult)}",
    "items=json.load(sys.stdin)",
    "for item in items:",
    "    request,response=models[item['method']]",
    "    request.model_validate(item['input']); response.model_validate(item['value'])",
    "print(json.dumps({'validated_methods':[item['method'] for item in items]}))",
  ].join("\n");
  const result = JSON.parse(execFileSync(python, ["-c", source], { shell: false, encoding: "utf8", input: JSON.stringify(captured),
    cwd: repository, timeout: 20000, env: { ...process.env, PYTHONPATH: join(repository, "src"), PYTHONDONTWRITEBYTECODE: "1" } }));
  expect(result.validated_methods).toEqual(cases.map(item => item.method));
});
