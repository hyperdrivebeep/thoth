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
  return <Callout compact className="answer-hold" intent={status === "PARTIAL_HOLD" ? "warning" : "none"} aria-label="결론 상태">
    <p>결론 상태: <strong>{label}</strong></p>
    {facts}
    {rows.length > 0 && <EvidenceUseSummary rows={rows}/>}
    <p>답할 수 있는 범위: {rows.length ? `기준 충족 ${supported}/${rows.length} · 확인 필요 ${needsCheck}` : "평가기준 정보를 아직 읽지 못했습니다"}</p>
    {counter && <p>{counter}</p>}
    {next && <p>다음 확보: {next}</p>}
  </Callout>;
}

