import { t } from "../i18n/locale";
import type { ReactNode } from "react";
import { Callout } from "@blueprintjs/core";
import type { CoverageMatrix } from "../api/researchFollowup";
import { coverageCounts } from "./coverageCounts";
import { EvidenceUseSummary } from "./EvidenceUseSummary";

/**
 * The first lines under an answer, in a fixed order: confirmed facts (slot) and what each answer check's
 * evidence can be used for. The conclusion status, the count of checks met and the counter-evidence search
 * are in the tags at the head of the card, and the action needed is in the review summary below; they are
 * not said a second time here. Only "no answer checks were recorded" is said here, because no tag says it.
 * An earlier round never gets its checks (the server sends them for the latest request only), so it says that
 * instead of "not yet".
 */
export function AnswerHold({ status, coverage, facts, earlierRound }: {
  status: string; coverage?: CoverageMatrix | null; facts?: ReactNode; earlierRound?: boolean;
}) {
  const { rows, total } = coverageCounts(coverage);
  return <Callout compact className="answer-hold" intent={status === "PARTIAL_HOLD" ? "warning" : "none"} role="group" aria-label={t("answerHold.title")}>
    {facts}
    {rows.length > 0 && <EvidenceUseSummary rows={rows}/>}
    {total === 0 && <p>{t("labels.answerScope")}{" "}{t(earlierRound ? "answerHold.coverageEarlier" : "answerHold.coverageMissing")}</p>}
  </Callout>;
}

