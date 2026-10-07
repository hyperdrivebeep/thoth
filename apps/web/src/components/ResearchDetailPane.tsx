import { Button } from "@blueprintjs/core";
import { useEffect, useRef, useState } from "react";
import type { PackRunResult } from "../types";
import { RecordInspector } from "./RecordInspector";
import type { ResearchDetail } from "./ResearchResultCard";
import { objectList, objectValue, textValue } from "../api/presentation";
import { ResearchHistoryWorkspace } from "./history/ResearchHistoryWorkspace";
import { ResultEvidencePanel } from "./ResultEvidencePanel";
import { HypothesisCompareLive } from "./HypothesisCompareLive";
import { ActionCompareLive } from "./ActionCompareLive";
import { ProjectMemoryPanel } from "./ProjectMemoryPanel";
import { hypothesisRows } from "./hypothesisView";
import { selectionKey } from "../api/historyModels";

const titles={evidence:"원문 근거",hypotheses:"가설 비교",reference:"참고값과 적용 조건",actions:"다음 행동 비교",map:"연구 지도",outcome:"실행과 관측 결과",memory:"프로젝트 기억",history:"변경 이력",diagnostics:"진단"};
export function ResearchDetailPane({detail,project,thread,onClose}: {
  detail:ResearchDetail;project:PackRunResult["project"];thread:PackRunResult["thread"];onClose:()=>void;
}) {
  const close=useRef<HTMLButtonElement>(null);
  const [mapView,setMapView]=useState<"evidence"|"execution"|"revision">("evidence");
  useEffect(()=>{close.current?.focus();},[]);
  const result=detail.result;
  const hypotheses=objectList(objectValue(result.portfolio).hypotheses);
  const actions=objectList(objectValue(result.action_plan).alternatives);
  const selectedEvidence = detail.historySelection?.kind === "result" && detail.historySelection.scope.projectId === project.project_id && detail.historySelection.scope.threadId === thread.thread_id
    ? <ResultEvidencePanel key={selectionKey(detail.historySelection)} selection={detail.historySelection} result={result} focusSpanId={detail.focusSpanId} citations={detail.citations}/>
    : <p role="alert">이 답변의 저장 기록 연결을 확인하지 못해 근거 원문을 읽을 수 없습니다.</p>;
  const history=detail.historySelection ? <ResearchHistoryWorkspace compact projectId={project.project_id} threadId={thread.thread_id} initialSelection={detail.historySelection}/>
    : <p>이 답변의 과거 요청 연결을 확인하지 못했습니다. 연구 이력에서 확인할 수 있습니다.</p>;
  return <aside className="research-detail-pane" aria-label={titles[detail.kind]} onKeyDown={e=>{if(e.key==="Escape"){e.stopPropagation();onClose();}}}>
    <header><h2>{titles[detail.kind]}</h2><Button ref={close} minimal small icon="cross" onClick={onClose} aria-label="상세 닫기" /></header>
    <div className="detail-scroll">
      {detail.kind==="evidence"&&selectedEvidence}
      {detail.kind==="hypotheses"&&<HypothesisCompareLive result={result} projectId={project.project_id} threadId={thread.thread_id}/>}
      {detail.kind==="actions"&&<ActionCompareLive result={result} projectId={project.project_id}/>}
      {detail.kind==="reference"&&<><p>참고 후보와 공식 기준을 구분합니다. 값이 없으면 임의의 수치를 채우지 않습니다.</p><RecordInspector value={result.reference??result.reference_analysis??result.reference_inquiry??result.criteria??null}/></>}
      {detail.kind==="outcome"&&<><p>실제 기록된 관측과 해석입니다. 실행 제안만 있는 경우 결과로 표시하지 않습니다.</p><RecordInspector value={result.outcome??result.r2_closed_loop??null}/></>}
      {detail.kind==="memory"&&<ProjectMemoryPanel projectId={project.project_id}/>}
      {detail.kind==="history"&&history}
      {detail.kind==="map"&&<><nav className="answer-actions">{([['evidence','근거·판단'],['execution','행동·결과'],['revision','변경 계보']] as const).map(([key,label])=><Button small minimal active={mapView===key} key={key} onClick={()=>setMapView(key)}>{label}</Button>)}</nav>
        {mapView==="evidence"&&<>{hypotheses.length===0?<p>연결된 가설 관계가 아직 없습니다.</p>:hypothesisRows(result).map((h,i)=><section className="detail-card" key={i}><h3>{h.statement}</h3><p>연결된 근거 {h.support.length}개</p><small>기록된 지지 근거 연결이며 확정된 인과관계가 아닙니다.</small></section>)}{selectedEvidence}</>}
        {mapView==="execution"&&<>{actions.map((a,i)=><p key={i}>제안: {textValue(a.specification)}</p>)}<RecordInspector value={result.outcome??result.r2_closed_loop??null}/></>}
        {mapView==="revision"&&history}</>}
      {detail.kind==="diagnostics"&&<RecordInspector value={result}/>}
    </div>
  </aside>;
}
