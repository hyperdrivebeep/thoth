import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Tag } from "@blueprintjs/core";
import type { CoverageMatrix } from "../api/researchFollowup";
import { coverageCounts, selectedEvidenceCount } from "./coverageCounts";
import { counterSearchSummary } from "./hypothesisView";
import { answerStatusLabel } from "./statusLabels";

/**
 * The counter-evidence search in one tag. Refuting sources can be on record although no search ran;
 * their count is kept on the tag, because nothing else on the card says it.
 */
function counterTagText(counter: { searched: number; total: number; counter: number }): string {
  if (counter.searched) return message("counterTag", counter.searched, counter.total, counter.counter);
  return counter.counter > 0 ? message("counterTagNotRunWith", counter.counter) : t("result.counterTagNotRun");
}

type StripTag = { text: string; intent: "none" | "warning" | "success" };

/**
 * The result's state in one line next to the THOTH heading: conclusion status, criteria met over total, the
 * counter-evidence search and the number of sources. Every number is the server's; nothing here is estimated.
 */
export function ResultStatusStrip({ result, coverage }: { result: Record<string, unknown>; coverage?: CoverageMatrix | null }) {
  const tags: StripTag[] = [];
  const status = typeof result.answer_status === "string" ? result.answer_status : null;
  if (status) tags.push({ text: answerStatusLabel(status) ?? status, intent: status === "PARTIAL_HOLD" ? "warning" : "none" });
  const counts = coverageCounts(coverage);
  if (counts.total > 0) tags.push({ text: message("coverageTag", counts.total, counts.satisfied, counts.needsCheck), intent: counts.needsCheck > 0 ? "warning" : "success" });
  const counter = counterSearchSummary(result);
  if (counter) tags.push({ text: counterTagText(counter), intent: "none" });
  const evidence = selectedEvidenceCount(result);
  if (evidence) tags.push({ text: message("evidenceTag", evidence), intent: "none" });
  if (!tags.length) return null;
  return <span className="result-status-strip" role="group" aria-label={t("result.statusStripLabel")}>
    {tags.map(tag => <Tag key={tag.text} minimal intent={tag.intent}>{tag.text}</Tag>)}
  </span>;
}
