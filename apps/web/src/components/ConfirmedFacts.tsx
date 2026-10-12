import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Tag } from "@blueprintjs/core";
import { numberSources } from "./answerText";

type Finding = { statement: string; refs: string[]; negative: boolean };

/** Reads only the stored structured list; stored entries that are malformed are skipped, never repaired. */
function readFindings(value: unknown): Finding[] {
  if (!Array.isArray(value)) return [];
  const found: Finding[] = [];
  for (const item of value as unknown[]) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as Record<string, unknown>;
    const refs = Array.isArray(entry.evidence_refs) ? entry.evidence_refs.filter((ref): ref is string => typeof ref === "string") : [];
    if (typeof entry.statement !== "string" || !entry.statement.trim() || refs.length === 0) continue;
    if (entry.kind !== "DOCUMENT_FACT" && entry.kind !== "VALID_NEGATIVE_FINDING") continue;
    found.push({ statement: entry.statement, refs, negative: entry.kind === "VALID_NEGATIVE_FINDING" });
  }
  return found;
}

/**
 * Facts the reviewer confirmed against the supplied documents: what a document states, and what was looked for and
 * not found. Taken from the stored result field only; results saved before the field existed say so.
 */
export function ConfirmedFacts({ result, onCite, heading = t("facts.title"), onlyWhenPresent = false }: {
  result: Record<string, unknown>;
  onCite: (spanId: string, numbers: Record<string, number>) => void;
  /** Wording for the heading, e.g. when the research stopped early. */
  heading?: string;
  /** Render nothing unless there is at least one stored fact. */
  onlyWhenPresent?: boolean;
}) {
  const stored = Object.hasOwn(result, "confirmed_findings");
  const findings = readFindings(result.confirmed_findings);
  const dropped = typeof result.confirmed_findings_dropped === "number" ? result.confirmed_findings_dropped : 0;
  const answer = typeof result.answer === "string" ? result.answer : "";
  const numbers = numberSources(answer, findings.flatMap(item => item.refs));
  const numberMap = Object.fromEntries(numbers);
  if (onlyWhenPresent && findings.length === 0) return null;
  return <section className="confirmed-facts" aria-label={t("facts.title")}>
    <p><strong>{heading}</strong>{findings.length > 0 && <small>{" "}{t("facts.documentOnly")}</small>}</p>
    {!stored ? <p className="muted">{t("facts.legacy")}</p>
      : findings.length === 0 ? <p className="muted">{t("facts.empty")}</p>
        : <ul>{findings.map((item, index) => <li key={index}>
          {item.negative && <Tag minimal>{t("facts.notFound")}</Tag>} {item.statement}{" "}
          {item.refs.map(ref => <button key={ref} type="button" className="answer-citation" title={ref} aria-label={formatMessage("evidenceButton", numbers.get(ref) ?? "?")}
            onClick={() => onCite(ref, numberMap)}>[{numbers.get(ref)}]</button>)}
        </li>)}</ul>}
    {dropped > 0 && <small className="muted">{formatMessage("excludedFacts", dropped)}</small>}
  </section>;
}

