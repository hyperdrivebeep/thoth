import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import type { ReactNode } from "react";
import { Callout } from "@blueprintjs/core";
import type { CoverageMatrix } from "../api/researchFollowup";
import { EvidenceUseSummary } from "./EvidenceUseSummary";
import { answerStatusLabel } from "./statusLabels";

/**
 * The first lines under an answer, in a fixed order: conclusion status, confirmed facts (slot), what each criterion's
 * evidence can be used for, how many criteria are fulfilled, the counter-evidence search, and what to secure next.
 */
export function AnswerHold({ status, coverage, facts, counter, next }: {
  status: string; coverage?: CoverageMatrix | null; facts?: ReactNode; counter?: string | null; next?: string | null;
}) {
  const label = answerStatusLabel(status) ?? status;
  const rows = coverage && coverage.availability !== "UNAVAILABLE" ? coverage.rows : [];
  const supported = rows.filter(row => row.status === "SATISFIED").length;
  const needsCheck = rows.filter(row => row.status === "UNRESOLVED" || row.status === "NOT_ASSESSED").length;
  return <Callout compact className="answer-hold" intent={status === "PARTIAL_HOLD" ? "warning" : "none"} aria-label={t("answerHold.title")}>
    <p>{t("labels.conclusion")}{" "}<strong>{label}</strong></p>
    {facts}
    {rows.length > 0 && <EvidenceUseSummary rows={rows}/>}
    <p>{t("labels.answerScope")}{" "}{rows.length ? formatMessage("answerCoverage", rows.length, supported, needsCheck) : t("answerHold.coverageMissing")}</p>
    {counter && <p>{counter}</p>}
    {next && <p>{t("labels.nextEvidence")}{" "}{next}</p>}
  </Callout>;
}

