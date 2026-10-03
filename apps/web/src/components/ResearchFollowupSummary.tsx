import { Button, Tag } from "@blueprintjs/core";
import type { CoverageMatrix, CoverageMatrixRow, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { Disclosure } from "./Disclosure";
import { currentnessLabel } from "./history/historyPresentation";
import { DecisionDeltaView } from "./history/DecisionDeltaComparison";
import { coverageTermLabel, nextActionLabel, statusLabel } from "./statusLabels";

function actionLabel(action?: NextUserAction | null): string {
  if (!action) return "다음 행동 기록 없음";
  return nextActionLabel(action);
}

function progressStateLabel(state: UserProgressSummary["state"]): string {
  switch (state) {
    case "COMPLETE": return "완료";
    case "HOLD": return "부분 보류";
    case "NEEDS_REVIEW": return "검토 필요";
    case "IN_PROGRESS": return "진행 중";
    case "PENDING_OR_NOT_PRODUCED": return "아직 결과 없음";
    case "FAILED": return "실행 실패";
    case "CANCELLED": return "취소됨";
    default: return "확인 불가";
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

/** Server progress keys with a user wording; anything else that looks like an internal id is only counted. */
const progressTerms: Record<string, string> = {
  "result:terminal": "답변이 최종 상태로 기록됐습니다",
  "result:checkpoint": "답변이 중간 점검 상태로 기록됐습니다",
  requirements_recorded: "평가 조건이 기록됐습니다",
  coverage_assessed: "조건별 충족 여부를 평가했습니다",
  RESULT_NOT_PRODUCED: "아직 답변이 만들어지지 않았습니다",
  LEGACY_BASIS_UNKNOWN: "이전 방식으로 저장된 답변이라 근거 기준을 확인할 수 없습니다",
  REQUIREMENT_SET_UNAVAILABLE: "평가 조건 목록을 읽지 못했습니다",
  COVERAGE_UNAVAILABLE: "조건별 평가 결과를 읽지 못했습니다",
  REQUIREMENT_NOT_ASSESSED: "아직 평가하지 못한 조건이 있습니다",
};
const STAGE_ID = /^research-stage:/;
const REQUIREMENT_ID = /^requirement:/;
// An ASCII token with no spaces that carries a separator or is all capitals is an internal code, not a sentence.
const CODE_LIKE = /^(?=.*[_:])[A-Za-z0-9_:.-]+$|^[A-Z][A-Z0-9]+$/;

function listItemLabel(item: string): string | null {
  if (progressTerms[item]) return progressTerms[item];
  const term = coverageTermLabel(item);
  return term !== item ? term : null;
}

function compactList(items: string[], empty: string) {
  const stages = items.filter(item => STAGE_ID.test(item));
  const requirements = items.filter(item => REQUIREMENT_ID.test(item));
  const rest = items.filter(item => !STAGE_ID.test(item) && !REQUIREMENT_ID.test(item));
  const plain = rest.map(item => listItemLabel(item) ?? (CODE_LIKE.test(item) ? null : item)).filter((item): item is string => item !== null);
  const codes = rest.filter(item => listItemLabel(item) === null && CODE_LIKE.test(item));
  const technical = [...stages, ...requirements, ...codes];
  if (!plain.length && !technical.length) return <p className="muted">{empty}</p>;
  return <>
    {plain.length > 0 && <ul>{plain.slice(0, 4).map(item => <li key={item}>{item}</li>)}</ul>}
    {stages.length > 0 && <p>검토 단계 {stages.length}개</p>}
    {requirements.length > 0 && <p>조건별 평가 {requirements.length}건</p>}
    {codes.length > 0 && <p>기록된 상세 코드 {codes.length}개</p>}
    {technical.length > 0 && <details className="connection-tech"><summary>기술 정보</summary><small>{technical.join(" · ")}</small></details>}
  </>;
}

function evidenceRefs(rows: CoverageMatrixRow[], answerEvidenceCount?: number) {
  const refs = Array.from(new Set(rows.flatMap(row => row.evidence_refs)));
  if (refs.length) return <><p>연결된 근거 {refs.length}개</p>
    <details className="connection-tech"><summary>기술 정보</summary><small>{refs.join(" · ")}</small></details></>;
  return <p className="muted">이 검토 요약에 따로 연결된 근거는 없습니다.{answerEvidenceCount ? ` 답변 근거 ${answerEvidenceCount}개는 '근거 원문'에서 볼 수 있습니다.` : ""}</p>;
}

function deltaLabel(delta?: DecisionDelta | null): string {
  if (!delta) return "비교 대상 답변이 확정되지 않아 바뀐 판단을 표시하지 않습니다.";
  if (delta.state === "NO_CHANGE") return "서버가 기록한 판단 변화가 없습니다.";
  if (delta.state === "UNAVAILABLE") return "현재 권한으로 판단 변화 기록을 읽을 수 없습니다.";
  if (delta.state === "PARTIAL") return "일부 판단 변화만 확인됐습니다.";
  return "서버가 기록한 판단 변화가 있습니다.";
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
  if (!progress && !coverage && !action && !delta) return null;
  const rows = coverage?.rows ?? [];
  const nextAction = action ?? progress?.next_user_action ?? null;
  const unresolved = rows.filter(row => row.status === "UNRESOLVED" || row.status === "NOT_ASSESSED");
  return <section className="followup-summary" aria-label="답변 검토 요약">
    <header>
      <div><h3>답변 검토</h3><p>서버가 기록한 진행 상태와 근거 충족 상태입니다.</p></div>
      {progress && <Tag minimal>{progressStateLabel(progress.state)}</Tag>}
    </header>
    {progress && <div className="followup-grid">
      <section><h4>검토 요약</h4>{compactList(progress.progress_items, "기록된 진행 요약이 없습니다.")}</section>
      <section><h4>직접 근거</h4>{evidenceRefs(rows, answerEvidenceCount)}{onOpenEvidence && <Button small minimal icon="search" onClick={onOpenEvidence}>근거 원문 열기</Button>}</section>
      <section><h4>바뀐 판단</h4><p>{deltaLabel(delta)}</p>{delta?.reason_state === "UNKNOWN_REASON" && <p className="muted">변경 이유는 저장된 기록에서 확인되지 않았습니다.</p>}</section>
      <section><h4>필요한 다음 행동</h4><p>{actionLabel(nextAction)}</p>{nextAction && <details className="connection-tech"><summary>기술 정보</summary><small>{nextAction.label}</small></details>}{nextAction?.requires_permission && <p className="result-notice">권한 결정이 필요한 행동입니다.</p>}</section>
    </div>}
    {progress && (progress.recorded_checks.length > 0 || progress.remaining_gaps.length > 0 || progress.unknowns.length > 0) && <Disclosure label="확인한 항목과 남은 gap">
      <div className="followup-detail-columns">
        <section><h4>기록된 확인</h4>{compactList(progress.recorded_checks, "기록된 확인 항목이 없습니다.")}</section>
        <section><h4>남은 gap</h4>{compactList(progress.remaining_gaps, "남은 gap이 없습니다.")}</section>
        <section><h4>확인 불가</h4>{compactList(progress.unknowns, "확인 불가 항목이 없습니다.")}</section>
      </div>
      <p className="muted">{currentnessLabel(progress.currentness)}</p>
    </Disclosure>}
    {coverage && <Disclosure defaultOpen={unresolved.length > 0} label={`평가기준 ${rows.length}개`}>
      {coverage.availability === "UNAVAILABLE" ? <p className="result-notice">현재 권한으로 평가기준 행을 읽을 수 없습니다.</p> : rows.length === 0 ? <p className="muted">이 답변에 연결된 평가기준 행이 없습니다.</p>
        : <div className="coverage-table" role="table" aria-label="평가기준 충족 상태">
          <div className="coverage-head" role="row"><span>기준</span><span>기준 상태</span><span>판단</span></div>
          {rows.map(row => <article className="coverage-row" role="row" key={row.requirement_id}>
            <div role="cell"><strong>{coverageTermLabel(row.target)}</strong><p>{row.question}</p></div>
            <div role="cell"><Tag minimal intent={coverageIntent(row.status)}>{coverageLabel(row.status)}</Tag></div>
            <div role="cell"><small>{[row.relation, row.validation].filter(term => term && term !== "NOT_ASSESSED").map(coverageTermLabel).join(" · ")
              || (row.blocker ? coverageTermLabel(row.blocker) : "추가 설명 없음")}</small></div>
            {(row.evidence_refs.length > 0 || row.reason_codes.length > 0) && <details className="connection-tech coverage-tech"><summary>기술 정보</summary>
              <small>{[...row.evidence_refs.slice(0, 3), ...row.reason_codes.slice(0, 3)].join(" · ")}</small></details>}
          </article>)}
        </div>}
    </Disclosure>}
    {delta && <DecisionDeltaView delta={delta} />}
  </section>;
}
