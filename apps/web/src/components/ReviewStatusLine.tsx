import { Button, Tag } from "@blueprintjs/core";
import { openRequest, outcomeLabel, relationText, resolvedRequest, type ReviewControls } from "./judgmentReview";

/** One hypothesis's review state: under review, not sent, resolved (previous to current), or a request button. */
export function ReviewStatusLine({ controls, hypothesisId, statement }: { controls: ReviewControls; hypothesisId: string; statement: string }) {
  const open = openRequest(controls.requests, hypothesisId);
  const done = resolvedRequest(controls.requests, hypothesisId);
  const target = { hypothesisId, statement, evidenceRef: null, evidenceLabel: "가설 전체" };
  if (open && (open.instruction_state === "FAILED" || open.instruction_state === "NOT_SENT")) {
    return <p className="review-line"><Tag minimal intent="warning">재검토 요청을 보내지 못했습니다</Tag>{" "}
      <Button small minimal icon="refresh" onClick={() => controls.onResend(open)}>다시 보내기</Button></p>;
  }
  return <div className="review-line">
    {open && <p><Tag minimal intent="primary" icon="refresh">재검토 중</Tag>
      {open.instruction_status === "QUEUED_AFTER_CURRENT" && <small> · 현재 조사 뒤에 반영</small>}</p>}
    {done?.resolution && <p>재검토 결과: <strong>{outcomeLabel(done.resolution.outcome)}</strong>
      <small> · 이전 {relationText(done.resolution.previous_relation)} → 현재 {relationText(done.resolution.resulting_relation)}</small></p>}
    {!open && <Button small minimal icon="comment" onClick={() => controls.onRequest(target)}>재검토 요청</Button>}
  </div>;
}
