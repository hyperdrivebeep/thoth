import { ReadableList } from "./ReadableList";
import { hypothesisStatusWord, reviewWord, type ReviewView } from "./hypothesisReview";
import type { HypothesisRow } from "./hypothesisView";

export const CAUSE_UNCONFIRMED_TEXT = "원인 미확인 — 지지된 가설이 없습니다(원인이 없다는 뜻이 아닙니다)";

/** Shown above the hypotheses when the review found none that holds. */
export function CauseUnconfirmed() {
  return <p className="result-notice" role="status">{CAUSE_UNCONFIRMED_TEXT}</p>;
}

function ReviewLines({ review }: { review: ReviewView }) {
  return <div className="hypothesis-review"><h4>검토 결과: {reviewWord(review.relation)}</h4>
    {review.explanation && <ReadableList items={[review.explanation]}/>}
    {review.gaps.length > 0 && <><small className="muted">남은 빈틈</small><ReadableList items={review.gaps}/></>}
  </div>;
}

/** What the hypothesis records beyond its statement. A value an old record does not have is hidden, except the observation. */
export function HypothesisFields({ row }: { row: HypothesisRow }) {
  const status = row.status ? hypothesisStatusWord(row.status) : null;
  return <>
    <p className="hypothesis-observed"><strong>관찰</strong> {row.observed || "기록 없음"}</p>
    <h3 className="hypothesis-inferred"><small className="muted">추론(가설)</small> {row.statement}</h3>
    {status && <small className="muted">가설 상태: {status}</small>}
    {row.assumptions.length > 0 && <><h4>가정</h4><ReadableList items={row.assumptions}/></>}
    {row.predicted.length > 0 && <><h4>맞다면 보일 관찰</h4><ReadableList items={row.predicted}/></>}
    {row.missing.length > 0 && <><h4>모자란 근거</h4><ReadableList items={row.missing}/></>}
    {row.review && <ReviewLines review={row.review}/>}
  </>;
}
