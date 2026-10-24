import { expectedUnknown } from "../api/expectedResultState";
import { Button } from "@blueprintjs/core";
import { message } from "../i18n/formatMessages";
import { objectList, stringValues, textValue } from "../api/presentation";
import { useTranslation, hypothesisLabel, evidenceCount } from "../i18n/locale";
import type { HypothesisRow } from "./hypothesisView";
import { expectedGrounding } from "./expectedGrounding";
import "./expectedResults.css";

const SHORT = 40;
const shortStatement = (text: string) => (text.length > SHORT ? text.slice(0, SHORT - 1) + "…" : text);

/** What the generator said each hypothesis would give for one test (contract v3), in words. An unconfirmed suggestion; no probability or score. */
export function ExpectedResults({ test, rows, onOpenEvidence }: {
  test: Record<string, unknown>; rows: HypothesisRow[]; onOpenEvidence?: (spanId: string) => void;
}) {
  const t = useTranslation();
  const table = objectList(test.expected_by_hypothesis).map(item => ({ item, grounding: expectedGrounding(item) }));
  if (table.length === 0) return null;
  return <details className="expected-results">
    <summary>{t("expected.title")}</summary>
    <ul role="list">{table.map(({ item, grounding }, at) => {
      const index = rows.findIndex(row => row.id === textValue(item.hypothesis_id));
      const basis = stringValues(item.basis);
      const sources = basis.length;
      const unknown = expectedUnknown(textValue(item.expected), item.knowledge_state, "annotation");
      const expected = unknown ? t("expected.unknown") : textValue(item.expected);
      const note = sources > 0 ? " · " + evidenceCount(sources) : unknown || grounding.unassessed ? "" : " · " + t("expected.unconfirmed");
      const statement = index >= 0 ? shortStatement(rows[index].statement.trim()) : "";
      return <li className="expected-result-row" key={at}>
        <div className="expected-result-label">{hypothesisLabel(index >= 0 ? index + 1 : "?") + (statement ? "(" + statement + ")" : "") + ": " + expected + note}</div>
        {grounding.role && <span className="expected-grounding-role">{t(grounding.role)}</span>}
        {grounding.rationale && <p className="expected-rationale">{grounding.rationale}</p>}
        {sources > 0 && grounding.unavailable && <p className="expected-basis-unavailable muted">{t("evidence.unknownBasis")}</p>}
        {onOpenEvidence && !grounding.unavailable && <div className="expected-evidence-actions">{basis.map((spanId, sourceIndex) => <Button key={`${spanId}:${sourceIndex}`} small minimal icon="document-open"
          aria-label={message("openHypothesisEvidence", index >= 0 ? index + 1 : "?", sourceIndex + 1)}
          onClick={() => onOpenEvidence(spanId)}>{message("openExpectedEvidence", sourceIndex + 1)}</Button>)}</div>}
      </li>;
    })}</ul>
    {table.some(row => row.grounding.unassessed) && <small className="expected-unassessed">{t("grounding.unassessed")}</small>}
    <small className="muted">{t("expected.notice")}</small>
  </details>;
}
