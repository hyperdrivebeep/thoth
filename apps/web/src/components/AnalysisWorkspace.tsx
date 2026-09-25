import { useState } from "react";
import { Button, Callout, Card } from "@blueprintjs/core";

import type { ActionCandidate, Hypothesis, PackRunResult } from "../types";
import { AuditTrail } from "./AuditTrail";
import { StatusBadge } from "./StatusBadge";

type WorkspaceView = "analysis" | "changes" | "memory" | "trust" | "lifecycle";

function Sufficiency({ result }: { result: PackRunResult }) {
  const assessment = result.cycle.assessment;
  const dimensions = [
    ["범위", assessment.scope_identity],
    ["공식 기준", assessment.criterion_authority],
    ["근거", assessment.evidence_coverage],
    ["비교 가능성", assessment.comparability],
  ] as const;
  return (
    <section className="result-section">
      <div className="section-title-row">
        <h2>정보 충분성</h2>
        <div className="badge-row">
          {assessment.derived_status.map((status) => (
            <StatusBadge key={status} value={status} />
          ))}
        </div>
      </div>
      <div className="dimension-grid">
        {dimensions.map(([label, value]) => (
          <Card className="dimension-card" key={label}>
            <span>{label}</span>
            <StatusBadge value={value.status} />
            <p>{value.reason}</p>
          </Card>
        ))}
      </div>
    </section>
  );
}

function HypothesisCard({ hypothesis }: { hypothesis: Hypothesis }) {
  return (
    <Card className="analysis-card">
      <div className="card-kicker">
        <StatusBadge value={hypothesis.primary_locus} />
        <code>{hypothesis.hypothesis_id}</code>
      </div>
      <h3>{hypothesis.statement}</h3>
      <p className="muted">불확실성: {hypothesis.uncertainty}</p>
      <dl>
        <dt>예상 관찰</dt>
        <dd>{hypothesis.predicted_observations.length ? hypothesis.predicted_observations.map((item,index)=><p key={index}>{item}</p>) : "아직 기록된 예측이 없습니다."}</dd>
        <dt>반대 근거 탐색</dt>
        <dd>{hypothesis.counterevidence_queries.length ? hypothesis.counterevidence_queries.map((item,index)=><p key={index}>{item}</p>) : "아직 기록된 반증 질의가 없습니다."}</dd>
        <dt>지지 근거 참조</dt><dd>{hypothesis.support_evidence_refs.join(" · ") || "연결된 지지 근거 없음"}</dd>
        {hypothesis.discriminating_tests.map((test,index) => (
          <div key={index}>
            <dt>구별 시험</dt>
            <dd>{test.procedure_candidate}</dd>
            <dt>참 / 대안</dt>
            <dd>
              {test.expected_if_true} / {test.expected_if_alternative}
            </dd>
            <dt>위험 등급</dt><dd><StatusBadge value={test.risk_tier}/></dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function ActionCard({ action, frontier }: { action: ActionCandidate; frontier: boolean }) {
  const protectedAction = action.execution_authority === "HUMAN_REQUIRED_R3";
  return (
    <Card className={`analysis-card action-card ${frontier ? "frontier" : ""}`}>
      <div className="card-kicker">
        <StatusBadge value={action.risk_tier} />
        <StatusBadge value={action.execution_authority} />
        {frontier && <span className="frontier-label">추천 frontier</span>}
      </div>
      <h3>{action.action_family.replaceAll("_", " ")}</h3>
      <p>{action.specification}</p>
      <p className="muted">정보가치: {action.expected_information_value}</p>
      <p className="muted">가역성: {action.reversibility} · 상태: {action.state}</p>
      <p className="muted">근거: {action.source_refs.join(" · ") || "연결된 근거 없음"}</p>
      {action.missing_evidence.length > 0 && (
        <p className="warning-copy">선행 필요: {action.missing_evidence.join(", ")}</p>
      )}
      {protectedAction && (
        <div className="protected-callout">
          이 카드에서 자동 실행하지 않습니다. 보호된 실행에는 정확한 대상·digest·권한 결정과 최종 preflight가 필요합니다.
        </div>
      )}
    </Card>
  );
}

export function AnalysisWorkspace({
  result,
  onCanonicalChange,
}: {
  result: PackRunResult;
  onCanonicalChange?: () => void;
}) {
  const [view, setView] = useState<WorkspaceView>("analysis");
  return (
    <div className="analysis-workspace">
      <header className="object-header">
        <div>
          <p className="eyebrow">DECISION OBJECT</p>
          <h1>{result.thread.current_object_ids[0]}</h1>
          <p>{result.thread.problem}</p>
        </div>
        <div className="badge-row">
          {result.scripted_model && <StatusBadge value="SCRIPTED_MODEL" />}
          <StatusBadge value={result.cycle.commit.disposition} />
        </div>
      </header>
      <nav className="workspace-tabs" aria-label="판단 객체 보기">
        {(
          [
            ["analysis", "판단"],
            ["changes", "변경 기록"],
            ["memory", "프로젝트 기억"],
            ["trust", "기준·권한·영수증"],
            ["lifecycle", "마감·내보내기"],
          ] as const
        ).map(([value, label]) => (
          <Button minimal
            className={view === value ? "active" : ""}
            key={value}
            onClick={() => setView(value)}
            type="button"
          >
            {label}
          </Button>
        ))}
      </nav>
      {view === "analysis" ? (
        <>
          <Sufficiency result={result} />
          <section className="result-section">
            <div className="section-title-row">
              <h2>경쟁가설 {result.cycle.portfolio.hypotheses.length}개</h2>
              <code>{result.cycle.portfolio.portfolio_id}</code>
            </div>
            <div className="card-grid">
              {result.cycle.portfolio.hypotheses.map((hypothesis) => (
                <HypothesisCard hypothesis={hypothesis} key={hypothesis.hypothesis_id} />
              ))}
            </div>
            {result.cycle.portfolio.hypotheses.length===0&&<Callout compact intent="warning">현재 가설이 없습니다. 부족한 근거와 다음 탐색을 확인하세요. 개수가 0이라는 이유만으로 연구를 실패나 완료로 처리하지 않습니다.</Callout>}
          </section>
          <section className="result-section">
            <div className="section-title-row">
              <h2>다음 행동 비교</h2>
              <span>{result.cycle.action_plan.alternatives.length}개 대안</span>
            </div>
            <div className="card-grid">
              {result.cycle.action_plan.alternatives.map((action) => (
                <ActionCard
                  action={action}
                  frontier={result.cycle.action_plan.frontier.includes(action.action_id)}
                  key={action.action_id}
                />
              ))}
            </div>
            {result.cycle.action_plan.alternatives.length===0&&<Callout compact>현재 제안된 행동이 없습니다. 미확인 조건과 다음 탐색을 먼저 확인하세요.</Callout>}
          </section>
        </>
      ) : (
        <AuditTrail onCanonicalChange={onCanonicalChange} result={result} view={view} />
      )}
    </div>
  );
}
