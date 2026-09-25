import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { evidenceResponse, manifest } from "./resultEvidenceTestFixture";

it("matches the actual selected-evidence Python DTOs without starting an engine", () => {
  const repository = fileURLToPath(new URL("../../../../", import.meta.url));
  const python = [join(repository, ".venv", "Scripts", "python.exe"), join(repository, ".venv", "bin", "python")].find(existsSync);
  if (!python) throw new Error("The repository Python environment is required for wire validation.");
  const cases = [0, 50, 100].map(offset => ({ input: { project_id: manifest.project_id, thread_id: manifest.thread_id,
    request_revision_digest: manifest.request_revision_digest, result_revision_digest: manifest.result_revision_digest,
    include_selected_evidence: true, selected_evidence_offset: offset, selected_evidence_limit: 50, contract_version: 2 },
    value: evidenceResponse(offset).value }));
  const source = ["import json,sys", "from thoth.domain.research_history import HistoricalResultInput, HistoricalResultView",
    "items=json.load(sys.stdin)", "counts=[]", "for item in items:",
    "    HistoricalResultInput.model_validate(item['input'])", "    value=HistoricalResultView.model_validate(item['value'])",
    "    counts.append(len(value.selected_evidence.items))", "print(json.dumps(counts))"].join("\n");
  const validated = JSON.parse(execFileSync(python, ["-c", source], { shell: false, encoding: "utf8", input: JSON.stringify(cases),
    cwd: repository, timeout: 20000, env: { ...process.env, PYTHONPATH: join(repository, "src"), PYTHONDONTWRITEBYTECODE: "1" } }));
  expect(validated).toEqual([50, 50, 49]);
});
