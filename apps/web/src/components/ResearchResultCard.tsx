import { Button } from "@blueprintjs/core";

export type DetailKind = "evidence" | "hypotheses" | "reference" | "actions" | "map" | "outcome" | "memory" | "history" | "diagnostics";
export type ResearchDetail = { kind: DetailKind; result: Record<string, unknown>; historySelection?: HistorySelection };
import { hasResearchContent, objectValue, objectList, stringValues, textValue } from "../api/presentation";
import type { ExecutionError, ResearchFailure } from "../api/research";
import type { Currentness, HistorySelection } from "../api/historyModels";
import type { CoverageMatrix, DecisionDelta, NextUserAction, UserProgressSummary } from "../api/researchFollowup";
import { currentnessLabel } from "./history/historyPresentation";
import { Disclosure } from "./Disclosure";
import { ResearchFollowupSummary } from "./ResearchFollowupSummary";

export function ResearchResultCard({ result, state, unavailable, error, failure, terminalReason, onDetail, historySelection, currentness, progressSummary, coverageMatrix, nextUserAction, decisionDelta }: {
  result: Record<string, unknown> | null; state: string; unavailable?: string; error?:ExecutionError | null; failure?:ResearchFailure | null; terminalReason?:string | null; onDetail: (detail: ResearchDetail) => void; historySelection?: HistorySelection; currentness?: Currentness;
  progressSummary?: UserProgressSummary | null; coverageMatrix?: CoverageMatrix | null; nextUserAction?: NextUserAction | null; decisionDelta?: DecisionDelta | null;
}) {
  const hypotheses = objectList(objectValue(result?.portfolio).hypotheses);
  const actions = objectList(objectValue(result?.action_plan).alternatives);
  const gaps = stringValues(objectValue(result?.assessment).missing_items);
  const failed=state==='FAILED';
  const cancelled=state==='CANCELLED';
  const cause=failure ?? error?.data?.failure;
  const code=cause?.primary.reason_code ?? error?.data?.reason_code ?? terminalReason ?? '';
  const interrupted=cancelled || failed || Boolean(terminalReason && terminalReason!=='BOUNDED_RESEARCH_COMPLETE');
  const publication=cause?.primary.origin==='RESULT_PUBLICATION'||cause?.secondary?.origin==='HOLD_PUBLICATION';
  const content=hasResearchContent(result);
  return <article className="assistant-message"><header>THOTH</header>
    {cancelled&&!unavailable&&<div className="result-notice" role="alert"><p>연구 실행이 취소되었습니다. 자동 재실행하지 않습니다.</p>
      <p>{code ? `기록된 종료 사유: ${code}` : "취소 사유는 현재 기록에서 확인되지 않습니다."}</p>
      {content ? <strong>취소 전까지 확인한 내용</strong> : <p>이 요청에 저장된 답변은 없습니다.</p>}
    </div>}
    {interrupted&&!cancelled&&!unavailable&&<div className="result-notice" role="alert"><p>연구를 완료하지 못했습니다. 마지막으로 저장된 자료와 질문은 보존돼 있습니다.</p>
      {/TIME_BUDGET|DEADLINE/.test(code)&&<p>모델 응답 시간 제한에 도달했습니다.</p>}
      {code==='TOTAL_RESEARCH_BUDGET_EXHAUSTED'&&<p>조사의 총호출 수 또는 총시간 제한에 도달했습니다. 누적 토큰 상한과는 별도 제한입니다.</p>}
      {/OAUTH_REQUEST_REJECTED_429/.test(code)&&<p>ChatGPT 사용 한도에 걸려 모델 호출이 거절됐습니다.</p>}
      {/AUTH_REQUIRED_403/.test(code)&&<p>모델 계정 인증이 거절됐습니다. 연결을 다시 확인하세요.</p>}
      {/MODEL_NOT_SUPPORTED/.test(code)&&<p>이 ChatGPT Codex 계정에서 지원하지 않는 모델입니다. 자동 재실행하지 않습니다.</p>}
      {/OAUTH_REQUEST_REJECTED_400/.test(code)&&<p>모델이 요청을 거절했습니다. 자동 재실행하지 않습니다.</p>}
      {publication&&<p>내부 결과 저장에 실패했습니다.</p>}
      {failed&&!publication&&!/TIME_BUDGET|DEADLINE|AUTH_REQUIRED|OAUTH_REQUEST_REJECTED|MODEL_NOT_SUPPORTED/.test(code)&&<p>내부 연구 처리에 실패했습니다.</p>}
      {(!cause || cause.remote_observation==='UNKNOWN')&&!/AUTH_REQUIRED|OAUTH_REQUEST_REJECTED|MODEL_NOT_SUPPORTED/.test(code)&&<p>원격 모델의 종료 여부는 확인되지 않았습니다. 자동 재실행하지 않습니다.</p>}
      {content&&<strong>실패 전까지 확인한 내용</strong>}
    </div>}
    {content && !unavailable && (currentness && currentness.state !== "CURRENT" ? <p className="result-notice">{currentnessLabel(currentness)} · 당시 저장된 답변입니다.</p>
      : state === "STALE" && <p className="result-notice">현재 기준으로 다시 확인이 필요한 과거 답변입니다.</p>)}
    {unavailable && <p className="result-notice">{unavailable}</p>}
    {result && content && !unavailable ? <>
      {typeof result.answer === "string" && <div className="answer-text">{result.answer}</div>}
      <ResearchFollowupSummary progress={progressSummary} coverage={coverageMatrix} action={nextUserAction} delta={decisionDelta}
        onOpenEvidence={historySelection ? () => onDetail({kind:"evidence",result,historySelection}) : undefined}/>
      {gaps.length > 0 && <div className="result-notice"><strong>아직 확인할 내용</strong><ul>{gaps.map((gap,index)=><li key={index}>{gap}</li>)}</ul></div>}
      {Object.hasOwn(objectValue(result.portfolio), "hypotheses") && <section className="conversation-findings"><h3>다른 설명과 확인할 점</h3>
        {hypotheses.length ? hypotheses.map((h,index)=><div key={textValue(h.hypothesis_id)||index}><p>{textValue(h.statement)}</p>{textValue(h.uncertainty)&&<small>{textValue(h.uncertainty)}</small>}</div>) : <p>현재 자료로 제안된 가설이 없습니다.</p>}
      </section>}
      {actions.length > 0 && <section className="conversation-findings"><h3>다음에 해볼 일</h3>{actions.map((action,index)=><div key={textValue(action.action_id)||index}><p>{textValue(action.specification)}</p><small>{textValue(action.expected_information_value)}</small>{action.execution_authority === "HUMAN_REQUIRED_R3" && <p className="result-notice">실행 제안입니다. 대상과 영향을 검토한 권한 결정이 필요합니다.</p>}</div>)}</section>}
      <nav className="answer-actions" aria-label="답변 상세">
        <Button small minimal icon="search" onClick={()=>onDetail({kind:"evidence",result,historySelection})}>근거 원문</Button>
        {Object.hasOwn(objectValue(result.portfolio), "hypotheses")&&<Button small minimal icon="lightbulb" onClick={()=>onDetail({kind:"hypotheses",result,historySelection})}>가설 비교</Button>}
        {actions.length>0&&<Button small minimal icon="play" onClick={()=>onDetail({kind:"actions",result,historySelection})}>행동 비교</Button>}
        {historySelection&&<Button small minimal icon="history" onClick={()=>onDetail({kind:"history",result,historySelection})}>변경 이력</Button>}
        <Disclosure className="answer-more" label="다른 상세 보기"><div className="answer-more-actions">{([
          ["reference","참고값·조건"],["map","연구 지도"],["outcome","실행 결과"],["memory","프로젝트 기억"],
        ] as const).map(([kind,label])=><Button small minimal key={kind} onClick={()=>onDetail({kind,result,historySelection})}>{label}</Button>)}</div></Disclosure>
      </nav>
    </> : !unavailable && !interrupted && <p className="muted">{state === "RUNNING" ? "자료와 조건을 확인하고 있습니다. 아직 저장된 답변은 없습니다." : "이 요청에 연결된 저장 답변이 아직 없습니다."}</p>}
  </article>;
}
