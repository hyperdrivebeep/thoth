import { Button, Callout } from "@blueprintjs/core";
import { objectList, objectValue, stringValues, textValue } from "../api/presentation";
import { ResearchResultCard } from "./ResearchResultCard";

const result = {
  answer: "현재 자료만으로는 보고 결과를 계획된 수용 조건과 직접 비교할 수 없습니다. 측정 환경과 평가 방법이 서로 달라 같은 조건의 결과인지 확인이 필요합니다.",
  assessment: { missing_items: ["동일한 측정 조건의 결과", "평가기준의 정의와 계산 방법"] },
  portfolio: { hypotheses: [
    { hypothesis_id: "h1", statement: "측정 환경의 차이가 결과 차이를 만들었을 수 있습니다.", uncertainty: "동일 조건 재측정이 필요합니다." },
    { hypothesis_id: "h2", statement: "평가 방법의 차이로 비교가 왜곡됐을 수 있습니다.", uncertainty: "계산 방법 확인이 필요합니다." },
  ] },
  action_plan: { alternatives: [
    { action_id: "a1", specification: "동일한 조건에서 결과를 다시 확보합니다.", expected_information_value: "수용 조건과 직접 비교할 수 있습니다.", execution_authority: "R1" },
    { action_id: "a2", specification: "평가기준과 계산 방법을 먼저 대조합니다.", expected_information_value: "비교 불가 원인을 좁힐 수 있습니다.", execution_authority: "R1" },
  ] },
};
const digest = "0".repeat(64);
const progressSummary = {
  schema_version: "1.0.0" as const,
  request_revision_digest: digest,
  result_revision_digest: digest,
  state: "HOLD" as const,
  currentness: { state: "CURRENT" as const, reasons: [], execution_eligible: true },
  progress_items: ["평가기준 2개 중 1개는 원문 근거와 연결됐고, 1개는 측정 조건이 달라 보류됐습니다."],
  recorded_checks: ["보고 결과의 측정 조건", "평가기준의 계산 기준"],
  remaining_gaps: ["같은 조건에서 산출된 결과"],
  unknowns: [],
  next_user_action: { schema_version: "1.0.0" as const, action_type: "REVIEW_GAPS" as const, label: "보류된 조건의 원문을 추가 확인", reason_codes: ["PARTIAL_HOLD"], requires_permission: false, target: null, basis: {} },
};
const coverageMatrix = {
  schema_version: "1.0.0" as const,
  request_revision_digest: digest,
  requirement_set_revision_digest: null,
  coverage_revision_digest: digest,
  availability: "AVAILABLE" as const,
  reason_codes: [],
  summary: { satisfied: 1, unresolved: 1, not_applicable: 0, not_assessed: 0, hold_targets: ["동일 조건 결과"], reason_codes: ["PARTIAL_HOLD"] },
  rows: [
    { requirement_id: "req-1", target: "평가기준 정의", question: "계산 기준이 원문에서 확인되는가", status: "SATISFIED" as const, applicability: "applies", relation: "direct", validation: "기준 정의가 연결 자료에 있음", blocker: "", review_refs: ["review:criteria"], evidence_refs: ["span:criteria-1"], reason_codes: [] },
    { requirement_id: "req-2", target: "동일 조건 결과", question: "결과가 같은 측정 조건인가", status: "UNRESOLVED" as const, applicability: "applies", relation: "needs_more_evidence", validation: "측정 환경이 달라 직접 비교 보류", blocker: "조건 일치 근거 없음", review_refs: ["review:gap"], evidence_refs: [], reason_codes: ["PARTIAL_HOLD"] },
  ],
};

function LegacyResultCard() {
  const hypotheses=objectList(objectValue(result.portfolio).hypotheses);
  const actions=objectList(objectValue(result.action_plan).alternatives);
  const gaps=stringValues(objectValue(result.assessment).missing_items);
  return <article className="assistant-message"><header>THOTH</header><div className="answer-text">{result.answer}</div>
    <div className="result-notice"><strong>아직 확인할 내용</strong><ul>{gaps.map((gap,index)=><li key={index}>{gap}</li>)}</ul></div>
    <section className="conversation-findings"><h3>검토할 설명</h3>{hypotheses.map((item,index)=><div key={index}><p>{textValue(item.statement)}</p><small>{textValue(item.uncertainty)}</small></div>)}</section>
    <section className="conversation-findings"><h3>다음에 해볼 일</h3>{actions.map((item,index)=><div key={index}><p>{textValue(item.specification)}</p><small>{textValue(item.expected_information_value)}</small></div>)}</section>
    <nav className="answer-actions" aria-label="답변 상세">{["근거","가설 비교","참고값·조건","행동 비교","연구 지도","결과","기억","변경 이력"].map(label=><Button small minimal key={label}>{label}</Button>)}</nav>
  </article>;
}

export default function ResultExample() {
  const before = new URLSearchParams(window.location.search).get("example") === "result-before";
  return <main className="bp6-dark result-example-page"><Callout intent="warning" title="검증용 결과 카드 · 실제 연구 데이터가 아닙니다">현재 컴포넌트의 정보 구조만 비교하는 합성 화면입니다.</Callout>
    <section className="conversation-timeline"><article className="user-message"><header>질문</header><p>보고 결과를 계획된 수용 조건과 비교할 수 없는 이유는 무엇인가요?</p></article>
      {before?<LegacyResultCard/>:<ResearchResultCard state="SUCCEEDED" result={result} progressSummary={progressSummary} coverageMatrix={coverageMatrix} nextUserAction={progressSummary.next_user_action}
        historySelection={{kind:"result",scope:{projectId:"preview",threadId:"preview",requestDigest:digest},operationId:"preview",resultDigest:digest}} onDetail={()=>undefined}/>}</section>
  </main>;
}
