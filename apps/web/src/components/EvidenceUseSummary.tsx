import { t } from "../i18n/locale";
import type { CoverageMatrixRow } from "../api/researchFollowup";
import { evidenceUseLabel, evidenceUseOrder, groupEvidenceUse } from "./evidenceUse";
import { statusLabel } from "./statusLabels";

/** One line of counts per evidence-use group; opening it lists each criterion with its own fulfilment state. */
export function EvidenceUseSummary({ rows }: { rows: CoverageMatrixRow[] }) {
  const groups = groupEvidenceUse(rows);
  const shown = evidenceUseOrder.filter(kind => kind !== "UNCLASSIFIED" || groups.UNCLASSIFIED.length > 0);
  return <div className="evidence-use" aria-label={t("evidenceUse.title")}>
    <p>{t("evidenceUse.title")}{" "}<small>{t("evidenceUse.ai")}</small>: {shown.map(kind => `${evidenceUseLabel[kind]} ${groups[kind].length}`).join(" · ")}</p>
    <details className="connection-tech"><summary>{t("evidenceUse.byCriterion")}</summary>
      {shown.filter(kind => groups[kind].length > 0).map(kind => <section key={kind}>
        <small>{evidenceUseLabel[kind]}</small>
        <ul>{groups[kind].map(row => <li key={row.requirement_id}>{row.question} · {statusLabel(row.status) ?? row.status}</li>)}</ul>
      </section>)}
    </details>
  </div>;
}

