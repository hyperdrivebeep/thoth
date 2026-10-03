import type { CoverageMatrixRow } from "../api/researchFollowup";
import { evidenceUseLabel, evidenceUseOrder, groupEvidenceUse } from "./evidenceUse";
import { statusLabel } from "./statusLabels";

/** One line of counts per evidence-use group; opening it lists each criterion with its own fulfilment state. */
export function EvidenceUseSummary({ rows }: { rows: CoverageMatrixRow[] }) {
  const groups = groupEvidenceUse(rows);
  const shown = evidenceUseOrder.filter(kind => kind !== "UNCLASSIFIED" || groups.UNCLASSIFIED.length > 0);
  return <div className="evidence-use" aria-label="근거 용도">
    <p>근거 용도 <small>(AI 검토 기준)</small>: {shown.map(kind => `${evidenceUseLabel[kind]} ${groups[kind].length}`).join(" · ")}</p>
    <details className="connection-tech"><summary>기준별 보기</summary>
      {shown.filter(kind => groups[kind].length > 0).map(kind => <section key={kind}>
        <small>{evidenceUseLabel[kind]}</small>
        <ul>{groups[kind].map(row => <li key={row.requirement_id}>{row.question} · {statusLabel(row.status) ?? row.status}</li>)}</ul>
      </section>)}
    </details>
  </div>;
}

