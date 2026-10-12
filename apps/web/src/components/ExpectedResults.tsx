import { expectedUnknown } from "../api/expectedResultState";
import { objectList, stringValues, textValue } from "../api/presentation";
import { useTranslation, hypothesisLabel, evidenceCount } from "../i18n/locale";
import type { HypothesisRow } from "./hypothesisView";

const SHORT = 40;
const shortStatement = (text: string) => (text.length > SHORT ? text.slice(0, SHORT - 1) + "…" : text);

/** What the generator said each hypothesis would give for one test (contract v3), in words. An unconfirmed suggestion; no probability or score. */
export function ExpectedResults({ test, rows }: { test: Record<string, unknown>; rows: HypothesisRow[] }) {
  const t = useTranslation();
  const table = objectList(test.expected_by_hypothesis);
  if (table.length === 0) return null;
  return <details className="expected-results">
    <summary>{t("expected.title")}</summary>
    <ul>{table.map((item, at) => {
      const index = rows.findIndex(row => row.id === textValue(item.hypothesis_id));
      const sources = stringValues(item.basis).length;
      const unknown = expectedUnknown(textValue(item.expected), item.knowledge_state, "annotation");
      const expected = unknown ? t("expected.unknown") : textValue(item.expected);
      const note = sources > 0 ? " · " + evidenceCount(sources) : unknown ? "" : " · " + t("expected.unconfirmed");
      const statement = index >= 0 ? shortStatement(rows[index].statement.trim()) : "";
      return <li key={at}>{hypothesisLabel(index >= 0 ? index + 1 : "?") + (statement ? "(" + statement + ")" : "") + ": " + expected + note}</li>;
    })}</ul>
    <small className="muted">{t("expected.notice")}</small>
  </details>;
}
