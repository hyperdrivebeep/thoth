import { UiError } from "../../i18n/UiError";
import { t } from "../../i18n/locale";
import { historyTitle } from "../../i18n/historyTitle";
import { Button, Callout, Tag } from "@blueprintjs/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { readHistoryEntry } from "../../api/history";
import { compareHistory } from "../../api/historyComparison";
import { restoreKey, selectionKey, type HistorySelection, type RestoreResult, type RestoreSelection } from "../../api/historyModels";
import { ChangeDetail } from "./HistoryChangeSummary";
import { HistoryContent } from "./HistoryContent";
import { RevisionComparison } from "./RevisionComparison";
import { RestorePreviewDialog } from "./RestorePreviewDialog";
import { currentnessLabel, currentnessReasonNote, currentnessTone, formatHistoryTime, kindLabel } from "./historyPresentation";
import { Disclosure } from "../Disclosure";

export function HistoryDetail({ selection, actorScope, onUseQuestion, onChooseResult }: {
  selection: HistorySelection; actorScope: string | null; onUseQuestion?: (question: string) => void;
  onChooseResult?: (side: "before" | "after", selection: HistorySelection) => void;
}) {
  const client = useQueryClient();
  const [compare, setCompare] = useState(false);
  const [restore, setRestore] = useState<RestoreSelection | null>(null);
  const restoreOpener = useRef<HTMLElement | null>(null);
  const restoreWasOpen = useRef(false);
  useEffect(() => {
    if (restore) restoreWasOpen.current = true;
    else if (restoreWasOpen.current) {
      restoreWasOpen.current = false;
      if (restoreOpener.current?.isConnected) restoreOpener.current.focus();
    }
  }, [restore]);
  const key = ["history-detail", selection.scope.projectId, actorScope, selectionKey(selection)];
  const detail = useQuery({ queryKey: key, queryFn: ({ signal }) => readHistoryEntry(selection, signal), retry: false, gcTime: 0 });
  const entry = detail.data;
  const comparison = useQuery({ queryKey: ["history-comparison", selection.scope.projectId, actorScope, selectionKey(selection), entry?.currentHead],
    enabled: compare && Boolean(entry?.restoreSelection), retry: false, gcTime: 0,
    queryFn: ({ signal }) => compareHistory(entry!.restoreSelection!, signal) });
  const refresh = async () => { await client.invalidateQueries({ queryKey: key }); };
  const applied = async (result: RestoreResult) => {
    // Invalidating raw record queries must not trigger their journaling transports.
    await client.invalidateQueries({ predicate: query => query.queryKey.includes(selection.scope.projectId), refetchType: "none" });
    const readback = await readHistoryEntry(selection);
    client.setQueryData(key, readback);
    await Promise.all(["research", "conversation", "research-history"].map(prefix =>
      client.invalidateQueries({ queryKey: [prefix, selection.scope.projectId] })));
    if (result.disposition === "APPLIED" && readback.currentHead !== result.newDigest) {
      throw new UiError("historyDetail.restoreChanged");
    }
  };
  if (detail.isPending) return <section className="history-detail"><p role="status">{t("historyDetail.loading")}</p></section>;
  if (detail.error || !entry) return <section className="history-detail"><Callout intent="warning" role="alert">{t("historyDetail.failed")}</Callout><Button icon="refresh" onClick={() => void detail.refetch()}>{t("historyDetail.reload")}</Button><Disclosure defaultOpen label={t("historyDetail.diagnostics")}><p>{detail.error?.message}</p></Disclosure></section>;
  const answer = typeof entry.result?.answer === "string" ? entry.result.answer : null;
  return <section className="history-detail" aria-label={t("historyDetail.selected")}>
    <header className="history-detail-heading"><span>{kindLabel(entry.kind)}<span aria-hidden="true"> · </span>{formatHistoryTime(entry.occurredAt)}</span><h2>{historyTitle(entry)}</h2>
      <Tag minimal intent={currentnessTone(entry.currentness)}>{currentnessLabel(entry.currentness)}</Tag>
      {currentnessReasonNote(entry.currentness) && <p className="history-coverage" role="status">{currentnessReasonNote(entry.currentness)}</p>}</header>
    {entry.availability === "UNAVAILABLE" ? <Callout compact intent="warning">{t("historyDetail.unavailable")}</Callout> : <>
      {entry.availability === "PARTIAL" && <p className="history-coverage" role="status">{t("historyDetail.partial")}</p>}
      {entry.executionState === "FAILED" && <Callout compact intent="warning">{t("historyDetail.failedRun")}</Callout>}
      {entry.executionState === "CANCELLED" && <Callout compact>{t("historyDetail.cancelled")}</Callout>}
      {(entry.executionState === "RUNNING" || entry.resultPhase === "CHECKPOINT") && <Callout compact>{t("historyDetail.checkpoint")}</Callout>}
      {entry.result?.answer_status === "PARTIAL_HOLD" && <p className="history-coverage">{t("historyDetail.heldAnswer")}</p>}
      {entry.question && <section className="history-question"><h3>{t("historyDetail.question")}</h3><p>{entry.question}</p></section>}
      {entry.kind === "RESULT" && <section className="history-answer"><h3>{t("historyDetail.answer")}</h3>{answer ? <p>{answer}</p> : <p className="history-help">{t("historyDetail.noAnswer")}</p>}</section>}
      <ChangeDetail summary={entry.changeSummary} />
      {entry.result && <HistoryContent content={entry.result} />}
      {entry.content && <HistoryContent content={entry.content} />}
      <div className="history-detail-actions">
        {selection.kind === "result" && selection.resultDigest && entry.availability !== "UNAVAILABLE" && onChooseResult && <>
          <Button icon="comparison" disabled={!actorScope} onClick={() => onChooseResult("before", selection)}>{t("historyDetail.chooseBefore")}</Button>
          <Button icon="comparison" disabled={!actorScope} onClick={() => onChooseResult("after", selection)}>{t("historyDetail.chooseAfter")}</Button>
        </>}
        {entry.restoreSelection && <Button icon="comparison" active={compare} onClick={() => setCompare(value => !value)}>{compare ? t("historyDetail.closeCompare") : t("historyDetail.compareCurrent")}</Button>}
        {entry.restoreSelection && entry.capability.previewSupported && <Button icon="history" disabled={!actorScope} onClick={event => { restoreOpener.current = event.currentTarget; setRestore(entry.restoreSelection); }}>{t("historyDetail.restorePreview")}</Button>}
        {entry.question && onUseQuestion && <Button minimal icon="edit" onClick={() => onUseQuestion(entry.question!)}>{t("historyDetail.useQuestion")}</Button>}
      </div>
      {!entry.capability.previewSupported && entry.kind !== "REQUEST" && <p className="history-help">{t("historyDetail.readOnly")}</p>}
      {entry.capability.previewSupported && !entry.capability.applyReady && <p className="history-help">{t("historyDetail.notReady")}</p>}
      {entry.kind === "RESULT" && !entry.currentHead && <p className="history-help">{t("historyDetail.noHead")}</p>}
      {compare && comparison.isFetching && <p role="status">{t("historyDetail.comparing")}</p>}
      {compare && comparison.error && <Callout compact intent="warning">{t("historyDetail.compareFailed")} <Button small onClick={() => void comparison.refetch()}>{t("delta.retry")}</Button></Callout>}
      {compare && comparison.data && <RevisionComparison comparison={comparison.data} />}
    </>}
    <Disclosure className="history-technical" label={t("historyDetail.technical")}><pre>{JSON.stringify(entry.technical, null, 2)}</pre></Disclosure>
    {restore && actorScope && <RestorePreviewDialog key={`${restoreKey(restore)}:${actorScope}`} scope={selection.scope} selection={restore} actorScope={actorScope} title={historyTitle(entry)}
      onClose={() => setRestore(null)} onApplied={applied} onRefresh={async () => { await refresh(); setRestore(null); }} />}
  </section>;
}
