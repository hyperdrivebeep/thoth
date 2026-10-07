import { Button, Callout, Classes, Dialog } from "@blueprintjs/core";
import { useQuery } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import type { EvidenceSpan } from "../types";
import { EvidenceRail } from "./EvidenceRail";

/** Opens one "근거 위치": the sentence it points at, highlighted among its document's sentences, or the reason it cannot be shown. */
export function TraceEvidenceDialog({ projectId, spanRef, onClose }: { projectId: string; spanRef: string | null; onClose: () => void }) {
  const query = useQuery({
    queryKey: ["trace-evidence-spans", projectId], enabled: spanRef !== null,
    queryFn: ({ signal }) => rpc<{ evidence: EvidenceSpan[] }>("evidence/list", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const spans = query.data?.value.evidence ?? [];
  const target = spans.find(span => span.span_id === spanRef);
  // In the order of the document, so the highlighted sentence sits where it is in the file.
  const byPosition = (a: EvidenceSpan, b: EvidenceSpan) => (a.locator.line ?? Number.MAX_SAFE_INTEGER) - (b.locator.line ?? Number.MAX_SAFE_INTEGER);
  const sameDocument = target ? spans.filter(span => span.artifact_id === target.artifact_id).sort(byPosition).slice(0, 300) : [];
  return <Dialog isOpen={spanRef !== null} onClose={onClose} title="근거 위치" className="trace-evidence-dialog">
    <div className={Classes.DIALOG_BODY}>
      <p className="muted">근거 위치는 이 판정에 연결된 자료의 위치 기록입니다. 연결되어 있다는 것만으로 THOTH가 그 문장을 근거로 확인한 것은 아닙니다.</p>
      {query.isPending && <p role="status">자료에서 위치를 찾는 중…</p>}
      {query.error && <Callout intent="warning" role="alert">자료 목록을 읽지 못해 이 위치를 열 수 없습니다. 위치가 없다는 뜻은 아닙니다.
        <Button small onClick={() => void query.refetch()}>다시 읽기</Button></Callout>}
      {query.data && !target && <Callout intent="warning" role="status">이 위치를 이 프로젝트에 연결된 자료에서 찾지 못했습니다. 자료를 연결하지 않았거나, 위치 표기가 THOTH가 만든 원문 위치가 아닐 수 있습니다.
        <p>기록된 위치 표기: <code>{spanRef}</code></p></Callout>}
      {target && <EvidenceRail evidence={sameDocument} focusSpanId={target.span_id} heading="이 위치의 문장 (강조 표시)"/>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}><Button onClick={onClose}>닫기</Button></div></div>
  </Dialog>;
}
