import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { useResultLanguage } from "../i18n/ResponseLanguageContext";
import { Button, Tag } from "@blueprintjs/core";
import type {
  CoverageMatrix,
  CoverageMatrixRow,
  DecisionDelta,
  NextUserAction,
  UserProgressSummary,
} from "../api/researchFollowup";
import { Disclosure } from "./Disclosure";
import { coverageCounts } from "./coverageCounts";
import { currentnessLabel } from "./history/historyPresentation";
import { DecisionDeltaView } from "./history/DecisionDeltaComparison";
import { coverageTermLabel, nextActionLabel, statusLabel } from "./statusLabels";
import { linkedRefs, requiredAction, rowWording, splitItems } from "./followupItems";

function progressStateLabel(state: UserProgressSummary["state"]): string {
  switch (state) {
    case "COMPLETE":
      return t("followup.complete");
    case "HOLD":
      return t("followup.partialHold");
    case "NEEDS_REVIEW":
      return t("followup.reviewNeeded");
    case "IN_PROGRESS":
      return t("workspace.running");
    case "PENDING_OR_NOT_PRODUCED":
      return t("followup.noResult");
    case "FAILED":
      return t("followup.failed");
    case "CANCELLED":
      return t("followup.cancelled");
    default:
      return t("followup.unverifiable");
  }
}

function coverageLabel(status: CoverageMatrixRow["status"]): string {
  return statusLabel(status) ?? status;
}

function coverageIntent(status: CoverageMatrixRow["status"]) {
  if (status === "SATISFIED") return "success" as const;
  if (status === "UNRESOLVED") return "warning" as const;
  if (status === "NOT_ASSESSED") return "none" as const;
  return "none" as const;
}

function compactList(items: string[], empty: string, responseLanguage?: string | null) {
  const { plain, stages, requirements, codes, sentences, technical } = splitItems(
    items,
    responseLanguage,
  );
  if (!plain.length && !technical.length) return <p className="muted">{empty}</p>;
  return (
    <>
      {plain.length > 0 && (
        <ul>
          {plain.slice(0, 4).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      {stages.length > 0 && <p>{message("reviewStages", stages.length)}</p>}
      {requirements.length > 0 && <p>{message("requirementAssessments", requirements.length)}</p>}
      {codes.length > 0 && <p>{message("technicalCodes", codes.length)}</p>}
      {sentences.length > 0 && <p>{message("technicalPhrases", sentences.length)}</p>}
    </>
  );
}

function evidenceRefs(rows: CoverageMatrixRow[], answerEvidenceCount?: number) {
  const refs = linkedRefs(rows);
  if (refs.length) return <p>{message("linkedEvidence", refs.length)}</p>;
  return (
    <p className="muted">
      {t("followup.noEvidence")}
      {answerEvidenceCount ? message("answerEvidence", answerEvidenceCount) : ""}
    </p>
  );
}

function deltaLabel(delta?: DecisionDelta | null): string {
  if (!delta) return t("followup.noComparison");
  if (delta.state === "NO_CHANGE") return t("followup.noChange");
  if (delta.state === "UNAVAILABLE") return t("followup.changesUnavailable");
  if (delta.state === "PARTIAL") return t("followup.partialChanges");
  return t("followup.hasChanges");
}

export function ResearchFollowupSummary({
  progress,
  coverage,
  action,
  delta,
  onOpenEvidence,
  answerEvidenceCount,
}: {
  answerEvidenceCount?: number;
  progress?: UserProgressSummary | null;
  coverage?: CoverageMatrix | null;
  action?: NextUserAction | null;
  delta?: DecisionDelta | null;
  onOpenEvidence?: () => void;
}) {
  const responseLanguage = useResultLanguage();
  if (!progress && !coverage && !action && !delta) return null;
  const rows = coverage?.rows ?? [];
  const nextAction = requiredAction(action, progress);
  const actionNeeded = nextAction !== null && nextAction.action_type !== "NONE";
  const { needsCheck } = coverageCounts(coverage);
  return (
    <section className="followup-summary" aria-label={t("followup.summaryLabel")}>
      <header>
        <div>
          <h3>{t("followup.title")}</h3>
          <p>{t("followup.statusNotice")}</p>
        </div>
        {progress && <Tag minimal>{progressStateLabel(progress.state)}</Tag>}
      </header>
      {progress && (
        <div className="followup-grid">
          <section>
            <h4>{t("followup.reviewSummary")}</h4>
            {compactList(progress.progress_items, t("followup.noProgress"), responseLanguage)}
          </section>
          <section>
            <h4>{t("followup.linkedEvidence")}</h4>
            {evidenceRefs(rows, answerEvidenceCount)}
            {onOpenEvidence && (
              <Button small minimal icon="search" onClick={onOpenEvidence}>
                {t("followup.openEvidence")}
              </Button>
            )}
          </section>
          <section>
            <h4>{t("followup.changes")}</h4>
            <p>{deltaLabel(delta)}</p>
            {delta?.reason_state === "UNKNOWN_REASON" && (
              <p className="muted">{t("followup.unknownChangeReason")}</p>
            )}
          </section>
          {actionNeeded && (
            <section>
              <h4>{t("followup.nextAction")}</h4>
              <p>{nextActionLabel(nextAction)}</p>
              {nextAction.requires_permission && (
                <p className="result-notice">{t("followup.authority")}</p>
              )}
            </section>
          )}
        </div>
      )}
      {progress &&
        (progress.recorded_checks.length > 0 ||
          progress.remaining_gaps.length > 0 ||
          progress.unknowns.length > 0) && (
          <Disclosure label={t("followup.checks")}>
            <div className="followup-detail-columns">
              <section>
                <h4>{t("followup.recordedChecks")}</h4>
                {compactList(progress.recorded_checks, t("followup.noChecks"), responseLanguage)}
              </section>
              <section>
                <h4>{t("followup.remaining")}</h4>
                {compactList(
                  progress.remaining_gaps,
                  t("followup.noneRemaining"),
                  responseLanguage,
                )}
              </section>
              <section>
                <h4>{t("followup.unverifiable")}</h4>
                {compactList(progress.unknowns, t("followup.noneUnknown"), responseLanguage)}
              </section>
            </div>
            <p className="muted">{currentnessLabel(progress.currentness)}</p>
          </Disclosure>
        )}
      {coverage && (
        <Disclosure defaultOpen={needsCheck > 0} label={message("answerChecks", rows.length)}>
          {coverage.availability === "UNAVAILABLE" ? (
            <p className="result-notice">{t("followup.inaccessibleChecks")}</p>
          ) : rows.length === 0 ? (
            <p className="muted">{t("followup.noAnswerChecks")}</p>
          ) : (
            <div className="coverage-table" role="table" aria-label={t("followup.checkStatus")}>
              <div className="coverage-head" role="row">
                <span role="columnheader">{t("followup.criterion")}</span>
                <span role="columnheader">{t("followup.criterionStatus")}</span>
                <span role="columnheader">{t("history.assessment")}</span>
              </div>
              {rows.map((row) => {
                const { text, title } = rowWording(row, responseLanguage);
                return (
                  <div className="coverage-row" role="row" key={row.requirement_id}>
                    <div role="cell">
                      {title !== null ? (
                        <strong>{title}</strong>
                      ) : (
                        text.question === null && (
                          <strong>{t("followup.assessmentCriterion")}</strong>
                        )
                      )}
                      {text.question !== null && <p>{text.question}</p>}
                    </div>
                    <div role="cell">
                      <Tag minimal intent={coverageIntent(row.status)}>
                        {coverageLabel(row.status)}
                      </Tag>
                    </div>
                    <div role="cell">
                      <small>
                        {[row.relation, row.validation]
                          .filter((term) => term && term !== "NOT_ASSESSED")
                          .map(coverageTermLabel)
                          .join(" · ") ||
                          (row.blocker
                            ? coverageTermLabel(row.blocker)
                            : t("followup.noExplanation"))}
                      </small>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Disclosure>
      )}
      {delta && <DecisionDeltaView delta={delta} />}
    </section>
  );
}
