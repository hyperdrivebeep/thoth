import { Button, Callout, Tag } from "@blueprintjs/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { readHistoryEntry } from "../../api/history";
import { compareHistory } from "../../api/historyComparison";
import { restoreKey, selectionKey, type HistorySelection, type RestoreResult, type RestoreSelection } from "../../api/historyModels";
import { HistoryContent } from "./HistoryContent";
import { RevisionComparison } from "./RevisionComparison";
import { RestorePreviewDialog } from "./RestorePreviewDialog";
import { currentnessLabel, currentnessTone, formatHistoryTime, kindLabel } from "./historyPresentation";
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
      throw new Error("복원은 기록됐지만 현재 버전이 달라졌거나 읽을 수 없습니다. 적용을 반복하지 않고 기록을 다시 확인하세요.");
    }
  };
  if (detail.isPending) return <section className="history-detail"><p role="status">선택한 기록을 읽는 중…</p></section>;
  if (detail.error || !entry) return <section className="history-detail"><Callout intent="warning" role="alert">선택한 기록을 읽지 못했습니다. 현재 답변으로 대신 표시하지 않습니다.</Callout><Button icon="refresh" onClick={() => void detail.refetch()}>기록 다시 읽기</Button><Disclosure defaultOpen label="조회 진단"><p>{detail.error?.message}</p></Disclosure></section>;
  const answer = typeof entry.result?.answer === "string" ? entry.result.answer : null;
  return <section className="history-detail" aria-label="선택한 연구 기록">
    <header className="history-detail-heading"><span>{kindLabel(entry.kind)}<span aria-hidden="true"> · </span>{formatHistoryTime(entry.occurredAt)}</span><h2>{entry.title}</h2>
      <Tag minimal intent={currentnessTone(entry.currentness)}>{currentnessLabel(entry.currentness)}</Tag></header>
    {entry.availability === "UNAVAILABLE" ? <Callout compact intent="warning">현재 권한이나 자료 상태로 이 기록의 내용을 읽을 수 없습니다.</Callout> : <>
      {entry.availability === "PARTIAL" && <p className="history-coverage" role="status">이 기록의 일부만 확인할 수 있습니다.</p>}
      {entry.executionState === "FAILED" && <Callout compact intent="warning">이 연구 실행은 실패했습니다. 아래에는 실패 전에 저장된 내용이 포함될 수 있습니다.</Callout>}
      {entry.executionState === "CANCELLED" && <Callout compact>중단된 연구의 저장 기록입니다.</Callout>}
      {(entry.executionState === "RUNNING" || entry.resultPhase === "CHECKPOINT") && <Callout compact>저장된 중간 기록입니다. 이 기록만으로 실행 종료를 판단하지 않습니다.</Callout>}
      {entry.result?.answer_status === "PARTIAL_HOLD" && <p className="history-coverage">일부 판단이 보류된 답변입니다. 부족한 근거와 적용 조건을 함께 확인하세요.</p>}
      {entry.question && <section className="history-question"><h3>당시 질문</h3><p>{entry.question}</p></section>}
      {entry.kind === "RESULT" && <section className="history-answer"><h3>당시 저장된 답변</h3>{answer ? <p>{answer}</p> : <p className="history-help">이 요청에 연결된 답변 본문이 없습니다. 부분 기록은 아래에서 확인할 수 있습니다.</p>}</section>}
      {entry.result && <HistoryContent content={entry.result} />}
      {entry.content && <HistoryContent content={entry.content} />}
      <div className="history-detail-actions">
        {selection.kind === "result" && selection.resultDigest && entry.availability !== "UNAVAILABLE" && onChooseResult && <>
          <Button icon="comparison" disabled={!actorScope} onClick={() => onChooseResult("before", selection)}>비교 전으로 선택</Button>
          <Button icon="comparison" disabled={!actorScope} onClick={() => onChooseResult("after", selection)}>비교 후로 선택</Button>
        </>}
        {entry.restoreSelection && <Button icon="comparison" active={compare} onClick={() => setCompare(value => !value)}>{compare ? "비교 닫기" : "현재와 비교"}</Button>}
        {entry.restoreSelection && entry.capability.previewSupported && <Button icon="history" disabled={!actorScope} onClick={event => { restoreOpener.current = event.currentTarget; setRestore(entry.restoreSelection); }}>복원 미리보기</Button>}
        {entry.question && onUseQuestion && <Button minimal icon="edit" onClick={() => onUseQuestion(entry.question!)}>질문을 입력창에 가져오기</Button>}
      </div>
      {!entry.capability.previewSupported && entry.kind !== "REQUEST" && <p className="history-help">이 기록은 열람용입니다. 직접 복원을 지원하는 항목에서만 복원 미리보기를 제공합니다.</p>}
      {entry.capability.previewSupported && !entry.capability.applyReady && <p className="history-help">복원할 내용은 미리 볼 수 있습니다. 현재 적용은 준비되지 않았습니다.</p>}
      {entry.kind === "RESULT" && !entry.currentHead && <p className="history-help">현재 버전의 연결을 확인하지 못해 이 답변의 버전 비교는 제공하지 않습니다.</p>}
      {compare && comparison.isFetching && <p role="status">두 버전을 비교하는 중…</p>}
      {compare && comparison.error && <Callout compact intent="warning">비교를 읽지 못했습니다. <Button small onClick={() => void comparison.refetch()}>다시 비교</Button></Callout>}
      {compare && comparison.data && <RevisionComparison comparison={comparison.data} />}
    </>}
    <Disclosure className="history-technical" label="세부 기록 · 진단"><pre>{JSON.stringify(entry.technical, null, 2)}</pre></Disclosure>
    {restore && actorScope && <RestorePreviewDialog key={`${restoreKey(restore)}:${actorScope}`} scope={selection.scope} selection={restore} actorScope={actorScope} title={entry.title}
      onClose={() => setRestore(null)} onApplied={applied} onRefresh={async () => { await refresh(); setRestore(null); }} />}
  </section>;
}
