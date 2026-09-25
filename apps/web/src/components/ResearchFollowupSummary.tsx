import { Button, Tag } from "@blueprintjs/core";
import type { CoverageMatrix, CoverageMatrixRow, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { Disclosure } from "./Disclosure";
import { currentnessLabel } from "./history/historyPresentation";
import { DecisionDeltaView } from "./history/DecisionDeltaComparison";

function actionLabel(action?: NextUserAction | null): string {
  if (!action) return "다음 행동 기록 없음";
  return action.label;
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
  switch (status) {
    case "SATISFIED": return "충족";
    case "UNRESOLVED": return "미해결";
    case "NOT_APPLICABLE": return "해당 없음";
    case "NOT_ASSESSED": return "미평가";
  }
}

function coverageIntent(status: CoverageMatrixRow["status"]) {
  if (status === "SATISFIED") return "success" as const;
  if (status === "UNRESOLVED") return "warning" as const;
  if (status === "NOT_ASSESSED") return "none" as const;
  return "none" as const;
}

function compactList(items: string[], empty: string) {
  return items.length ? <ul>{items.slice(0, 4).map(item => <li key={item}>{item}</li>)}</ul> : <p className="muted">{empty}</p>;
}

function evidenceRefs(rows: CoverageMatrixRow[]) {
  const refs = Array.from(new Set(rows.flatMap(row => row.evidence_refs))).slice(0, 6);
  return refs.length ? <div className="followup-ref-list">{refs.map(ref => <code key={ref}>{ref}</code>)}</div> : <p className="muted">이 검토 요약에 연결된 직접 근거 ref가 아직 기록되지 않았습니다.</p>;
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
}: {
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
      <section><h4>직접 근거</h4>{evidenceRefs(rows)}{onOpenEvidence && <Button small minimal icon="search" onClick={onOpenEvidence}>근거 원문 열기</Button>}</section>
      <section><h4>바뀐 판단</h4><p>{deltaLabel(delta)}</p>{delta?.reason_state === "UNKNOWN_REASON" && <p className="muted">변경 이유는 저장된 기록에서 확인되지 않았습니다.</p>}</section>
      <section><h4>필요한 다음 행동</h4><p>{actionLabel(nextAction)}</p>{nextAction?.requires_permission && <p className="result-notice">권한 결정이 필요한 행동입니다.</p>}</section>
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
          {rows.map(row => <article className="coverage-row" key={row.requirement_id}>
            <div><Tag minimal intent={coverageIntent(row.status)}>{coverageLabel(row.status)}</Tag><strong>{row.target}</strong></div>
            <p>{row.question}</p>
            <small>{row.validation || row.blocker || row.relation || "추가 설명 없음"}</small>
            {(row.evidence_refs.length > 0 || row.reason_codes.length > 0) && <div className="followup-ref-list">
              {row.evidence_refs.slice(0, 3).map(ref => <code key={ref}>{ref}</code>)}
              {row.reason_codes.slice(0, 3).map(code => <span key={code}>{code}</span>)}
            </div>}
          </article>)}
        </div>}
    </Disclosure>}
    {delta && <DecisionDeltaView delta={delta} />}
  </section>;
}
