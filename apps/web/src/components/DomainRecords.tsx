import { UiError } from "../i18n/UiError";
import { t } from "../i18n/locale";
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
    if (!view.method) throw new UiError("records.noQuery");
    return rpc<Record<string,unknown>>(view.method,
      {project_id:projectId,...(view.scope === "thread" ? {thread_id:threadId} : {}),...(view.scope === "operation" ? {operation_id:operation} : {})},crypto.randomUUID());
    },
  });
  return <div className="domain-detail"><div className="section-title-row"><div><h2>{view.label}</h2><p className="muted">{view.description}</p></div><Tag minimal>{view.namespace}</Tag></div>
    {view.note && <Callout compact intent="warning">{view.note}</Callout>}
    {view.scope === "operation" && <InputGroup aria-label={t("records.operationId")} placeholder="operation ID" value={operation} onChange={event=>{setOperation(event.target.value);read.reset();}}/>}
    <div className="catalog-toolbar"><Button icon="refresh" intent="primary" loading={read.isPending} disabled={blocked} onClick={()=>read.mutate()}>{t("records.load")}</Button><Button minimal icon="code" onClick={()=>onOpenCatalog(view.namespace)}>{t("records.controls")}</Button></div>
    {view.method && <Callout compact title={view.method}>{transportFor(view.method)==="/rpc/query" ? t("records.readOnly") : t("records.journaled")}</Callout>}
    {view.scope === "thread"&&!threadId && <Callout compact intent="warning">{t("records.chooseTask")}</Callout>}
    {read.error && <Callout intent="danger" title={t("records.failed")}><p>{read.error.message}</p><p>{t("records.moreInputs")}</p></Callout>}
    {read.data ? <div className="record-panel"><div className="section-title-row"><h2>{view.label}</h2><Tag>{read.data.state}</Tag></div><RecordInspector value={read.data.value} /></div> : !read.error && <div className="records-empty"><h2>{view.label}{" "}{t("records.beforeRead")}</h2><p>{t("records.noAutoRun")}</p></div>}
  </div>;
}

export function DomainRecords({ projectId, threadId, operationId = "", onOpenCatalog }: { projectId: string; threadId: string; operationId?:string; onOpenCatalog: (namespace:string) => void }) {
  const [selected, setSelected] = useState("object");
  const view = visibleRecordViews.find(item => item.namespace === selected) ?? visibleRecordViews[0];
  return <section className="domain-records"><header className="workspace-heading"><div><p className="eyebrow">CANONICAL RECORDS</p><h1>{t("records.title")}</h1><p>{t("records.intro")}</p></div></header>
    <div className="domain-grid" aria-label={t("records.area")}>{visibleRecordViews.map(item=><Button minimal active={selected===item.namespace} alignText="left" className="domain-card" key={item.namespace} onClick={()=>setSelected(item.namespace)}>
      <Icon icon={item.method?"database":"lock"}/><span><strong>{item.label}</strong><small>{item.namespace}</small></span></Button>)}</div>
    <DomainRecordDetail key={`${projectId}:${threadId}:${view.namespace}`} projectId={projectId} threadId={threadId} operationId={operationId} view={view} onOpenCatalog={onOpenCatalog}/>
  </section>;
}
