import { Button, Callout } from "@blueprintjs/core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { readResultEvidence, resultContentKey, type ResultSelection, type EvidencePagePosition } from "../api/resultEvidence";
import { selectionKey } from "../api/historyModels";
import { EvidenceRail } from "./EvidenceRail";
import { useMemo } from "react";
import { consistentEvidencePages, useResultEvidenceProtection } from "./useResultEvidenceProtection";
import { Disclosure } from "./Disclosure";

export function ResultEvidencePanel({ selection, result }: { selection: ResultSelection; result: Record<string, unknown> }) {
  const contentKey = useMemo(() => resultContentKey(result), [result]);
  const queryKey = ["result-evidence", selection.scope.projectId, selectionKey(selection), contentKey];
  const protection = useResultEvidenceProtection(queryKey);
  const query = useInfiniteQuery({ queryKey,
    initialPageParam: { offset: 0, resultDigest: selection.resultDigest } as EvidencePagePosition,
    queryFn: async ({ pageParam, signal }) => {
      try { return await readResultEvidence(selection, result, pageParam, signal); }
      catch (error) { if (!signal.aborted) protection.block(); throw error; }
    },
    getNextPageParam: page => page.next_offset === null ? undefined : { offset: page.next_offset, resultDigest: page.result_revision_digest },
    retry: false, gcTime: 0, staleTime: 0, refetchOnMount: "always",
  });
  if (query.isFetching || query.isPending) return <p role="status">이 답변의 근거를 읽는 중…</p>;
  if (protection.reason === "SCOPE_CHANGED") return <Callout intent="warning" role="alert">접근 범위 또는 기록 연결이 바뀌었습니다. 상세 화면을 닫고 근거를 다시 열어 주세요.</Callout>;
  if (query.error || protection.blocked || !query.data || query.data.pages.length === 0) return <Callout intent="warning" role="alert">이 답변의 근거를 읽지 못했습니다. 근거가 없다는 뜻은 아닙니다.
    <Button small onClick={() => void query.refetch()}>근거 다시 읽기</Button><Disclosure defaultOpen label="조회 진단"><p>{query.error?.message}</p></Disclosure></Callout>;
  const pages = query.data.pages;
  const first = pages[0];
  if (!consistentEvidencePages(pages)) {
    return <Callout intent="warning" role="alert">접근 범위 또는 기록 연결이 바뀌었습니다. 상세 화면을 닫고 근거를 다시 열어 주세요.</Callout>;
  }
  const items = pages.flatMap(page => page.items);
  const available = items.flatMap(item => item.span ? [item.span] : []);
  const unavailable = items.filter(item => item.availability !== "AVAILABLE");
  return <section aria-label="답변에 선택된 근거">
    <h3>이 답변에 선택된 근거 {first.selected_count}개</h3>
    <p>연구에 선택된 원문 목록입니다. 답변 본문에서 직접 인용한 개수와 다를 수 있습니다.</p>
    {first.selected_count === 0 ? <p>이 저장 버전에 선택된 근거가 없습니다.</p> : <>
      {available.length > 0 && <EvidenceRail evidence={available} heading={`확인된 원문 ${available.length}개`}/>}
      {unavailable.map(item => <p className="result-notice" key={item.span_id}>{item.availability === "BASIS_MISMATCH" ? "저장 당시의 자료 버전이나 원문 해시와 일치하지 않아 표시하지 않습니다."
        : item.availability === "UNKNOWN_BASIS" ? "저장 당시의 자료 기준을 확인할 수 없어 원문을 표시하지 않습니다." : "현재 읽을 수 없는 근거입니다."}<br/><small>{item.span_id}</small></p>)}
      <p>전체 {first.selected_count}개 중 {items.length}개 확인 · 원문 표시 {available.length}개 · 원문 확인 불가 {unavailable.length}개</p>
      {query.hasNextPage && <Button onClick={() => void query.fetchNextPage()}>다음 근거 보기</Button>}
    </>}
  </section>;
}
