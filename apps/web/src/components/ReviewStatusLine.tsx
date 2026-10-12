import { t } from "../i18n/locale";
import { Button, Tag } from "@blueprintjs/core";
import { openRequest, outcomeLabel, relationText, resolvedRequest, type ReviewControls } from "./judgmentReview";

/** One hypothesis's review state: under review, not sent, resolved (previous to current), or a request button. */
export function ReviewStatusLine({ controls, hypothesisId, statement }: { controls: ReviewControls; hypothesisId: string; statement: string }) {
  const open = openRequest(controls.requests, hypothesisId);
  const done = resolvedRequest(controls.requests, hypothesisId);
  const target = { hypothesisId, statement, evidenceRef: null, get evidenceLabel() { return t("reviewStatus.all"); } };
  if (open && (open.instruction_state === "FAILED" || open.instruction_state === "NOT_SENT")) {
    return <p className="review-line"><Tag minimal intent="warning">{t("reviewStatus.failed")}</Tag>{" "}
      <Button small minimal icon="refresh" onClick={() => controls.onResend(open)}>{t("reviewStatus.resend")}</Button></p>;
  }
  return <div className="review-line">
    {open && <p><Tag minimal intent="primary" icon="refresh">{t("reviewStatus.running")}</Tag>
      {open.instruction_status === "QUEUED_AFTER_CURRENT" && <small>{" "}{t("reviewStatus.afterCurrent")}</small>}</p>}
    {done?.resolution && <p>{t("labels.recheckResult")}{" "}<strong>{outcomeLabel(done.resolution.outcome)}</strong>
      <small>{" "}{t("labels.beforeArrow")}{" "}{relationText(done.resolution.previous_relation)}{" "}{t("labels.afterArrow")}{" "}{relationText(done.resolution.resulting_relation)}</small></p>}
    {!open && <Button small minimal icon="comment" onClick={() => controls.onRequest(target)}>{t("reviewStatus.request")}</Button>}
  </div>;
}
