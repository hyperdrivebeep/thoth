import { t } from "../i18n/locale";
import { Button, Callout, Classes, Dialog } from "@blueprintjs/core";
import { useQuery } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import type { EvidenceSpan } from "../types";
import { EvidenceRail } from "./EvidenceRail";
import { useReadAccessProtection } from "./useReadAccessProtection";

/** Opens one t("trace.field.sourceRefs"): the sentence it points at, highlighted among its document's sentences, or the reason it cannot be shown. */
export function TraceEvidenceDialog({ projectId, spanRef, onClose }: { projectId: string; spanRef: string | null; onClose: () => void }) {
  const query = useQuery({
    queryKey: ["trace-evidence-spans", projectId], enabled: spanRef !== null,
    queryFn: ({ signal }) => rpc<{ evidence: EvidenceSpan[] }>("evidence/list", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const blocked = useReadAccessProtection(["trace-evidence-spans", projectId], query.error);
  const data = blocked ? undefined : query.data;
  const spans = data?.value.evidence ?? [];
  const target = spans.find(span => span.span_id === spanRef);
  // In the order of the document, so the highlighted sentence sits where it is in the file.
  const byPosition = (a: EvidenceSpan, b: EvidenceSpan) => (a.locator.line ?? Number.MAX_SAFE_INTEGER) - (b.locator.line ?? Number.MAX_SAFE_INTEGER);
  const sameDocument = target ? spans.filter(span => span.artifact_id === target.artifact_id).sort(byPosition).slice(0, 300) : [];
  return <Dialog isOpen={spanRef !== null} onClose={onClose} title={t("trace.field.sourceRefs")} className="trace-evidence-dialog">
    <div className={Classes.DIALOG_BODY}>
      <p className="muted">{t("traceEvidence.notice")}</p>
      {query.isPending && <p role="status">{t("traceEvidence.loading")}</p>}
      {(query.error || blocked) && <Callout intent="warning" role="alert">{t("traceEvidence.failed")}<Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button></Callout>}
      {data && !target && <Callout intent="warning" role="status">{t("traceEvidence.notFound")}<p>{t("labels.locator")}{" "}<code>{spanRef}</code></p></Callout>}
      {target && <EvidenceRail evidence={sameDocument} focusSpanId={target.span_id} heading={t("traceEvidence.passage")}/>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}><Button onClick={onClose}>{t("hold.close")}</Button></div></div>
  </Dialog>;
}
