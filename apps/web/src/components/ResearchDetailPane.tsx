import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { ResponseLanguageContext, resultLanguage } from "../i18n/ResponseLanguageContext";
import { Button } from "@blueprintjs/core";
import { useEffect, useRef, useState } from "react";
import type { PackRunResult } from "../types";
import { RecordInspector } from "./RecordInspector";
import type { ResearchDetail } from "./ResearchResultCard";
import { objectList, objectValue, textValue } from "../api/presentation";
import { ResearchHistoryWorkspace } from "./history/ResearchHistoryWorkspace";
import { ResultEvidencePanel } from "./ResultEvidencePanel";
import { HypothesisEvidencePanel } from "./HypothesisEvidencePanel";
import { resultContentKey } from "../api/resultEvidence";
import { ActionCompareLive } from "./ActionCompareLive";
import { ProjectMemoryPanel } from "./ProjectMemoryPanel";
import { hypothesisRows } from "./hypothesisView";
import { selectionKey } from "../api/historyModels";

const titles={get evidence() { return t("detail.evidence"); },get hypotheses() { return t("result.compareHypotheses"); },get reference() { return t("detail.references"); },get actions() { return t("detail.actions"); },get map() { return t("result.map"); },get outcome() { return t("detail.outcomes"); },get memory() { return t("result.memory"); },get history() { return t("result.changes"); },get diagnostics() { return t("detail.diagnostics"); }};
export function ResearchDetailPane({detail,project,thread,onClose}: {
  detail:ResearchDetail;project:PackRunResult["project"];thread:PackRunResult["thread"];onClose:()=>void;
}) {
  const close=useRef<HTMLButtonElement>(null);
  const [mapView,setMapView]=useState<"evidence"|"execution"|"revision">("evidence");
  useEffect(()=>{close.current?.focus();},[]);
  const result=detail.result;
  const hypotheses=objectList(objectValue(result.portfolio).hypotheses);
  const actions=objectList(objectValue(result.action_plan).alternatives);
  const evidenceSelection = detail.historySelection?.kind === "result" && detail.historySelection.scope.projectId === project.project_id && detail.historySelection.scope.threadId === thread.thread_id
    ? detail.historySelection : undefined;
  const selectedEvidence = evidenceSelection
    ? <ResultEvidencePanel key={selectionKey(evidenceSelection)} selection={evidenceSelection} result={result} focusSpanId={detail.focusSpanId} citations={detail.citations}/>
    : <p role="alert">{t("detail.evidenceLinkMissing")}</p>;
  const history=detail.historySelection ? <ResearchHistoryWorkspace compact projectId={project.project_id} threadId={thread.thread_id} initialSelection={detail.historySelection}/>
    : <p>{t("detail.historyLinkMissing")}</p>;
  return <ResponseLanguageContext.Provider value={resultLanguage(result)}><aside className="research-detail-pane" aria-label={titles[detail.kind]} onKeyDown={e=>{if(e.key==="Escape"){e.stopPropagation();onClose();}}}>
    <header><h2>{titles[detail.kind]}</h2><Button ref={close} minimal small icon="cross" onClick={onClose} aria-label={t("delta.closeDetails")} /></header>
    <div className="detail-scroll">
      {detail.kind==="evidence"&&selectedEvidence}
      {detail.kind==="hypotheses"&&<HypothesisEvidencePanel
        key={JSON.stringify([project.project_id, thread.thread_id, detail.historySelection ? selectionKey(detail.historySelection) : null, resultContentKey(result)])}
        result={result} projectId={project.project_id} threadId={thread.thread_id} selection={evidenceSelection} citations={detail.citations}/>}
      {detail.kind==="actions"&&<ActionCompareLive result={result} projectId={project.project_id}/>}
      {detail.kind==="reference"&&<><p>{t("detail.referenceNotice")}</p><RecordInspector value={result.reference??result.reference_analysis??result.reference_inquiry??result.criteria??null}/></>}
      {detail.kind==="outcome"&&<><p>{t("detail.outcomeNotice")}</p><RecordInspector value={result.outcome??result.r2_closed_loop??null}/></>}
      {detail.kind==="memory"&&<ProjectMemoryPanel projectId={project.project_id}/>}
      {detail.kind==="history"&&history}
      {detail.kind==="map"&&<><nav className="answer-actions">{([['evidence',t("detail.evidenceAssessment")],['execution',t("detail.actionsResults")],['revision',t("detail.lineage")]] as const).map(([key,label])=><Button small minimal active={mapView===key} key={key} onClick={()=>setMapView(key)}>{label}</Button>)}</nav>
        {mapView==="evidence"&&<>{hypotheses.length===0?<p>{t("detail.noRelations")}</p>:hypothesisRows(result).map((h,i)=><section className="detail-card" key={i}><h3>{h.statement}</h3><p>{formatMessage("linkedEvidence", h.support.length)}</p><small>{t("detail.supportNotice")}</small></section>)}{selectedEvidence}</>}
        {mapView==="execution"&&<>{actions.map((a,i)=><p key={i}>{t("labels.proposal")}{" "}{textValue(a.specification)}</p>)}<RecordInspector value={result.outcome??result.r2_closed_loop??null}/></>}
        {mapView==="revision"&&history}</>}
      {detail.kind==="diagnostics"&&<RecordInspector value={result}/>}
    </div>
  </aside></ResponseLanguageContext.Provider>;
}
