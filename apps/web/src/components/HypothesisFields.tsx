import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
import { ReadableList } from "./ReadableList";
import { hypothesisStatusWord, reviewWord, type ReviewView } from "./hypothesisReview";
import type { HypothesisRow } from "./hypothesisView";

export const CAUSE_UNCONFIRMED_TEXT = messages["hypothesisFields.unconfirmed"].ko;

/** Shown above the hypotheses when the review found none that holds. */
export function CauseUnconfirmed() {
  return <p className="result-notice" role="status">{t("hypothesisFields.unconfirmed")}</p>;
}

function ReviewLines({ review }: { review: ReviewView }) {
  return <div className="hypothesis-review"><h4>{t("labels.reviewResult")}{" "}{reviewWord(review.relation)}</h4>
    {review.explanation && <ReadableList items={[review.explanation]}/>}
    {review.gaps.length > 0 && <><small className="muted">{t("hypothesisFields.gaps")}</small><ReadableList items={review.gaps}/></>}
  </div>;
}

/** What the hypothesis records beyond its statement. A value an old record does not have is hidden, except the observation. */
export function HypothesisFields({ row }: { row: HypothesisRow }) {
  const status = row.status ? hypothesisStatusWord(row.status) : null;
  return <>
    <p className="hypothesis-observed"><strong>{t("tracePage.observation")}</strong> {row.observed || t("delta.noRecord")}</p>
    <h3 className="hypothesis-inferred"><small className="muted">{t("hypothesisFields.inference")}</small> {row.statement}</h3>
    {status && <small className="muted">{t("labels.hypothesisState")}{" "}{status}</small>}
    {row.assumptions.length > 0 && <><h4>{t("hypothesisFields.assumptions")}</h4><ReadableList items={row.assumptions}/></>}
    {row.predicted.length > 0 && <><h4>{t("hypothesisFields.predictions")}</h4><ReadableList items={row.predicted}/></>}
    {row.missing.length > 0 && <><h4>{t("hypothesisFields.missing")}</h4><ReadableList items={row.missing}/></>}
    {row.review && <ReviewLines review={row.review}/>}
  </>;
}
