import { Button, Callout, Icon, InputGroup, Tag } from "@blueprintjs/core";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";
import { transportFor } from "../api/capabilities";
import { RecordInspector } from "./RecordInspector";
import { visibleRecordViews, type RecordView } from "../api/recordViews";

function DomainRecordDetail({projectId,threadId,operationId,view,onOpenCatalog}: {projectId:string;threadId:string;operationId:string;view:RecordView;onOpenCatalog:(namespace:string)=>void}) {
  const [operation,setOperation] = useState(operationId);
  const blocked = !view.method || (view.scope === "thread" && !threadId) || (view.scope === "operation" && !operation.trim());
  const read = useMutation({ mutationFn: () => {
    if (!view.method) throw new Error("이 namespace에는 비변경 조회 계약이 없습니다.");
    return rpc<Record<string,unknown>>(view.method,
      {project_id:projectId,...(view.scope === "thread" ? {thread_id:threadId} : {}),...(view.scope === "operation" ? {operation_id:operation} : {})},crypto.randomUUID());
    },
  });
  return <div className="domain-detail"><div className="section-title-row"><div><h2>{view.label}</h2><p className="muted">{view.description}</p></div><Tag minimal>{view.namespace}</Tag></div>
    {view.note && <Callout compact intent="warning">{view.note}</Callout>}
    {view.scope === "operation" && <InputGroup aria-label="조회할 operation ID" placeholder="operation ID" value={operation} onChange={event=>{setOperation(event.target.value);read.reset();}}/>}
    <div className="catalog-toolbar"><Button icon="refresh" intent="primary" loading={read.isPending} disabled={blocked} onClick={()=>read.mutate()}>기록 불러오기</Button><Button minimal icon="code" onClick={()=>onOpenCatalog(view.namespace)}>이 영역의 상세 조회 · 고급 제어</Button></div>
    {view.method && <Callout compact title={view.method}>{transportFor(view.method)==="/rpc/query" ? "비변경 조회입니다." : "서버의 /rpc 기록형 조회입니다. 이 버튼을 누를 때만 operation·journal이 생성될 수 있습니다."}</Callout>}
    {view.scope === "thread"&&!threadId && <Callout compact intent="warning">작업을 먼저 선택하세요.</Callout>}
    {read.error && <Callout intent="danger" title="조회 실패 또는 필요한 범위 누락"><p>{read.error.message}</p><p>세부 ID나 범위가 필요한 계약은 전체 기능의 입력 편집기에서 조회할 수 있습니다.</p></Callout>}
    {read.data ? <div className="record-panel"><div className="section-title-row"><h2>{view.label}</h2><Tag>{read.data.state}</Tag></div><RecordInspector value={read.data.value} /></div> : !read.error && <div className="records-empty"><h2>{view.label} 조회 전</h2><p>이 탭을 여는 것만으로 작업을 실행하지 않습니다.</p></div>}
  </div>;
}

export function DomainRecords({ projectId, threadId, operationId = "", onOpenCatalog }: { projectId: string; threadId: string; operationId?:string; onOpenCatalog: (namespace:string) => void }) {
  const [selected, setSelected] = useState("object");
  const view = visibleRecordViews.find(item => item.namespace === selected) ?? visibleRecordViews[0];
  return <section className="domain-records"><header className="workspace-heading"><div><p className="eyebrow">CANONICAL RECORDS</p><h1>상세 기록</h1><p>개발·진단을 위한 영역별 저장 기록과 API 조회입니다.</p></div></header>
    <div className="domain-grid" aria-label="제품 영역">{visibleRecordViews.map(item=><Button minimal active={selected===item.namespace} alignText="left" className="domain-card" key={item.namespace} onClick={()=>setSelected(item.namespace)}>
      <Icon icon={item.method?"database":"lock"}/><span><strong>{item.label}</strong><small>{item.namespace}</small></span></Button>)}</div>
    <DomainRecordDetail key={`${projectId}:${threadId}:${view.namespace}`} projectId={projectId} threadId={threadId} operationId={operationId} view={view} onOpenCatalog={onOpenCatalog}/>
  </section>;
}
