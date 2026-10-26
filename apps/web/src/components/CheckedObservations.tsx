import { t } from "../i18n/locale";
import { objectList, objectValue, stringValues, textValue } from "../api/presentation";

/** Source arithmetic is separate from model findings and official criteria. */
export function CheckedObservations({ result, onCite }: {
  result: Record<string, unknown>;
  onCite: (spanId: string) => void;
}) {
  const payload = objectValue(result.checked_observations);
  if (payload.schema_version !== "1.0.0" || payload.kind !== "SOURCE_ARITHMETIC") return null;
  const series = objectList(payload.series);
  const byBinding = new Map(series.map(item => [textValue(item.binding_digest), item]));
  const rows: Record<string, unknown>[] = Array.isArray(payload.display_rows) ? objectList(payload.display_rows)
    : objectList(payload.calculations).map((row): Record<string, unknown> => {
      const owner = byBinding.get(textValue(row.binding_digest)) ?? {};
      return { ...row, label: owner.label, context_label: owner.context_label, size_label: owner.size_label };
    });
  const diagnosticCount = typeof payload.diagnostic_count === "number" ? payload.diagnostic_count : objectList(payload.diagnostics).length;
  const notDelivered = typeof payload.not_delivered === "number" ? payload.not_delivered
    : objectList(payload.deliveries).reduce((sum, item) => sum + (typeof item.not_delivered === "number" ? item.not_delivered : 0), 0);
  return <details className="conversation-findings checked-observations">
    <summary>{t("numeric.title")}</summary>
    <p>{t("numeric.limitation")}</p>
    {rows.length > 0 && <table className="bp6-html-table bp6-html-table-condensed">
      <thead><tr><th>{t("numeric.series")}</th><th>{t("numeric.interval")}</th><th>{t("numeric.operation")}</th><th>{t("numeric.value")}</th><th>{t("numeric.source")}</th></tr></thead>
      <tbody>{rows.map((row, index) => {
        const operation = row.operation === "RATIO" ? t("numeric.ratio") : row.operation === "DIFFERENCE" ? t("numeric.difference") : row.operation === "LOG_LOG_SLOPE" ? t("numeric.slope") : null;
        if (operation === null) return null;
        return <tr key={textValue(row.calculation_ref) || textValue(row.input_digest) || index}>
          <td>{textValue(row.label)}<br/><small>{textValue(row.context_label)}</small></td>
          <td>{stringValues(row.sizes).join(" → ")} {textValue(row.size_label)}</td>
          <td>{operation}</td>
          <td>{row.status === "CALCULATED" ? textValue(row.display_value) : t("numeric.notCalculated")}{row.operation === "DIFFERENCE" && row.status === "CALCULATED" ? ` ${textValue(row.unit)}` : ""}</td>
          <td>{stringValues(row.operand_refs).map((ref, sourceIndex) => <button type="button" key={ref} className="answer-citation" onClick={() => onCite(ref)}>{t("numeric.source")} {sourceIndex + 1}</button>)}</td>
        </tr>;
      })}</tbody>
    </table>}
    {diagnosticCount > 0 && <p className="muted">{t("numeric.unsupported")}</p>}
    {notDelivered > 0 && <p className="muted">{t("numeric.notDelivered")}</p>}
  </details>;
}
