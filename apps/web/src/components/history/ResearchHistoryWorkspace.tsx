import { Button, Callout, Icon, Switch } from "@blueprintjs/core";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { readHistoryPage } from "../../api/history";
import { RpcError } from "../../api/rpcClient";
import { selectionKey, type HistoryKind, type HistoryScope, type HistorySelection } from "../../api/historyModels";
import { HistoryDetail } from "./HistoryDetail";
import { HistoryTimeline } from "./HistoryTimeline";
import { Disclosure } from "../Disclosure";
import { ProjectReviewList } from "./ProjectReviewList";
import { DecisionDeltaComparison } from "./DecisionDeltaComparison";

function ScopedHistory({ scope, initialSelection, onUseQuestion, kinds }: {
  scope: HistoryScope; initialSelection?: HistorySelection; onUseQuestion?: (question: string) => void; kinds?: HistoryKind[];
}) {
  const client = useQueryClient();
  const [selection, setSelection] = useState<HistorySelection | null>(initialSelection ?? null);
  const [before, setBefore] = useState<HistorySelection | null>(null);
  const [after, setAfter] = useState<HistorySelection | null>(null);
  const [comparisonActorScope, setComparisonActorScope] = useState<string | null>(null);
  const [historyCheckEpoch, setHistoryCheckEpoch] = useState(0);
  const previousHistory = useRef({ updatedAt: 0, pageCount: 0 });
  const [showCheckpoints, setShowCheckpoints] = useState(false);
  const queryKey = ["research-history", scope.projectId, scope.threadId ?? null, scope.requestDigest ?? null, kinds?.join(",") ?? "all"];
  const query = useInfiniteQuery({ queryKey, initialPageParam: null as string | null, retry: false, gcTime: 0,
    queryFn: ({ pageParam, signal }) => readHistoryPage(scope, pageParam, signal, kinds), getNextPageParam: page => page.nextCursor ?? undefined });
  const pages = query.data?.pages ?? [];
  const actorScope = pages[0]?.actorScope ?? null;
  const contextChanged = pages.some(page => page.actorScope !== actorScope);
  const historyError = Boolean(query.error);
  useEffect(() => { setBefore(null); setAfter(null); setComparisonActorScope(null); }, [actorScope, contextChanged, historyError]);
  useEffect(() => {
    const previous = previousHistory.current;
    if (previous.updatedAt > 0 && query.dataUpdatedAt > 0 && query.dataUpdatedAt !== previous.updatedAt && pages.length <= previous.pageCount) {
      setHistoryCheckEpoch(value => value + 1);
    }
    previousHistory.current = { updatedAt: query.dataUpdatedAt, pageCount: pages.length };
  }, [query.dataUpdatedAt, pages.length]);
  const invalidCursor = query.error instanceof RpcError && query.error.details.reason_code === "HISTORY_CURSOR_INVALID";
  const refresh = () => { setSelection(null); setBefore(null); setAfter(null); setComparisonActorScope(null); void client.resetQueries({ queryKey, exact: true }); };
  const chooseComparison = (side: "before" | "after", next: HistorySelection) => {
    if (!actorScope || next.kind !== "result" || !next.resultDigest) return;
    setComparisonActorScope(actorScope);
    if (side === "before") setBefore(next); else setAfter(next);
    setSelection(next);
  };
  return <>
    <div className="history-toolbar"><p>질문과 답변, 판단이 달라진 과정을 확인하세요.</p><div className="history-toolbar-actions"><Switch label="중간 저장 포함" checked={showCheckpoints} onChange={event=>setShowCheckpoints(event.currentTarget.checked)}/><Button minimal icon="refresh" onClick={refresh} loading={query.isRefetching}>새로 읽기</Button></div></div>
    {query.error && <Callout intent="warning" role="alert">{invalidCursor ? "이 조회 구간은 더 이상 이어서 읽을 수 없습니다. 새로 읽으면 첫 구간부터 다시 불러옵니다." : "연구 이력을 읽지 못했습니다. 서버 연결과 이력 조회 지원 상태를 확인해 주세요."}<Button small onClick={refresh}>다시 읽기</Button><Disclosure defaultOpen label="조회 진단"><p>{query.error.message}</p></Disclosure></Callout>}
    {contextChanged && <Callout intent="warning" role="alert">로그인 또는 접근 범위가 바뀌었습니다. 이력을 새로 읽어 주세요.<Button small onClick={refresh}>새로 읽기</Button></Callout>}
    {!initialSelection && !kinds && <ProjectReviewList projectId={scope.projectId} onSelect={next => { setSelection(next); setBefore(null); setAfter(null); }} />}
    {!contextChanged && !query.error && pages.length > 0 && <section className="history-decision-comparison" aria-label="답변 비교 선택">
      <div className="section-title-row"><div><h2>두 답변 비교</h2><p>연구 이력의 답변에서 비교 전과 비교 후를 각각 선택하세요.</p></div>
        {(before || after) && <Button small minimal onClick={() => { setBefore(null); setAfter(null); }}>비교 해제</Button>}</div>
      <div className="history-compare-selected"><span>비교 전: {comparisonActorScope === actorScope && before?.kind === "result" ? before.title ?? "선택한 답변" : "선택 전"}</span><span>비교 후: {comparisonActorScope === actorScope && after?.kind === "result" ? after.title ?? "선택한 답변" : "선택 전"}</span></div>
      {comparisonActorScope === actorScope && before && after && <DecisionDeltaComparison key={JSON.stringify([actorScope, selectionKey(before), selectionKey(after)])}
        before={before} after={after} actorScope={actorScope} historyCheckEpoch={historyCheckEpoch} suspended={query.isRefetching} />}
    </section>}
    {!contextChanged && <div className="history-layout">
      {query.isPending ? <section className="history-timeline"><p role="status">연구 이력을 읽는 중…</p></section>
        : query.error && pages.length === 0 ? <section className="history-timeline"><p>기록의 유무를 확인하지 못했습니다.</p></section> : <HistoryTimeline pages={pages} selected={selection}
        showCheckpoints={showCheckpoints} loadingMore={query.isFetchingNextPage} onMore={() => void query.fetchNextPage()} onSelect={row => { setSelection(row.selection); setBefore(null); setAfter(null); }}
        comparison={{ before: comparisonActorScope === actorScope ? before : null, after: comparisonActorScope === actorScope ? after : null, onChoose: (side, row) => chooseComparison(side, row.selection) }} />}
      {selection ? <HistoryDetail key={`${actorScope}:${selectionKey(selection)}`} selection={selection} actorScope={actorScope} onUseQuestion={onUseQuestion}
          onChooseResult={chooseComparison} />
        : <section className="history-detail history-select-prompt"><Icon icon="document-open" size={32} /><h2>기록을 선택해 주세요</h2><p>당시 결과를 읽고, 현재와 달라진 내용을 비교할 수 있습니다.</p><span>복원은 내용을 확인한 뒤 별도로 적용합니다.</span></section>}
    </div>}
  </>;
}

export function ResearchHistoryWorkspace({ projectId, threadId, requestDigest, initialSelection, onUseQuestion, compact = false, kinds }: {
  projectId: string; threadId?: string; initialSelection?: HistorySelection;
  onUseQuestion?: (question: string) => void; compact?: boolean; kinds?: HistoryKind[]; requestDigest?: string;
}) {
  const [allProject, setAllProject] = useState(!threadId);
  const scope: HistoryScope = initialSelection?.scope ?? { projectId, ...(!allProject && threadId ? { threadId } : {}), ...(requestDigest ? { requestDigest } : {}) };
  if (scope.projectId !== projectId) return <Callout intent="warning">선택한 기록의 프로젝트가 일치하지 않습니다.</Callout>;
  return <section className={`research-history ${compact ? "history-compact" : ""}`} aria-label="연구 이력">
    {import.meta.env.VITE_THOTH_TEST_MODE === "true" && <Callout compact intent="warning" className="history-test-banner">검증 모드 · 통제된 자료와 모델로 확인하는 화면입니다. 실제 연구 결과 검증이 아닙니다.</Callout>}
    <header className="workspace-heading history-heading"><div><p className="eyebrow">RESEARCH HISTORY</p><h1>{kinds?.length === 1 && kinds[0] === "MEMORY" ? "프로젝트 기억" : "연구 이력"}</h1><p>당시의 답변과 근거를 보존하고, 바뀐 판단을 살펴봅니다.</p></div>
      {!initialSelection && !requestDigest && threadId && <div className="history-scope" aria-label="이력 범위"><Button small active={!allProject} onClick={() => setAllProject(false)}>현재 세션</Button><Button small active={allProject} onClick={() => setAllProject(true)}>프로젝트 전체</Button></div>}
    </header>
    <ScopedHistory key={JSON.stringify([scope, kinds, initialSelection ? selectionKey(initialSelection) : null])} scope={scope} initialSelection={initialSelection} onUseQuestion={onUseQuestion} kinds={kinds} />
  </section>;
}
