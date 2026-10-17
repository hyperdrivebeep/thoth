import { t } from "../../i18n/locale";
import { Button, Callout, Icon, Switch } from "@blueprintjs/core";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { readHistoryPage } from "../../api/history";
import { RpcError } from "../../api/rpcClient";
import { selectionKey, type HistoryKind, type HistoryScope, type HistorySelection } from "../../api/historyModels";
import { HistoryDetail } from "./HistoryDetail";
import { HistoryTimeline } from "./HistoryTimeline";
import { Disclosure } from "../Disclosure";
import { eyebrowLabel } from "../statusLabels";
import { ProjectReviewList } from "./ProjectReviewList";
import { DecisionDeltaComparison } from "./DecisionDeltaComparison";
import { useReadAccessProtection } from "../useReadAccessProtection";

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
  const blocked = useReadAccessProtection(queryKey, query.error);
  const pages = blocked ? [] : query.data?.pages ?? [];
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
    <div className="history-toolbar"><p>{t("historyWorkspace.intro")}</p><div className="history-toolbar-actions"><Switch label={t("historyWorkspace.checkpoints")} checked={showCheckpoints} onChange={event=>setShowCheckpoints(event.currentTarget.checked)}/><Button minimal icon="refresh" onClick={refresh} loading={query.isRefetching}>{t("historyWorkspace.reload")}</Button></div></div>
    {(query.error || blocked) && <Callout intent="warning" role="alert">{invalidCursor ? t("historyWorkspace.cursorExpired") : t("historyWorkspace.failed")}<Button small onClick={refresh}>{t("workspace.reload")}</Button><Disclosure defaultOpen label={t("historyDetail.diagnostics")}><p>{query.error?.message}</p></Disclosure></Callout>}
    {contextChanged && <Callout intent="warning" role="alert">{t("historyWorkspace.scopeChanged")}<Button small onClick={refresh}>{t("historyWorkspace.reload")}</Button></Callout>}
    {!initialSelection && !kinds && <ProjectReviewList projectId={scope.projectId} onSelect={next => { setSelection(next); setBefore(null); setAfter(null); }} />}
    {!contextChanged && !query.error && pages.length > 0 && <section className="history-decision-comparison" aria-label={t("historyWorkspace.selection")}>
      <div className="section-title-row"><div><h2>{t("historyWorkspace.compare")}</h2><p>{t("historyWorkspace.choosePair")}</p></div>
        {(before || after) && <Button small minimal onClick={() => { setBefore(null); setAfter(null); }}>{t("historyWorkspace.clearPair")}</Button>}</div>
      <div className="history-compare-selected"><span>{t("labels.before")}{" "}{comparisonActorScope === actorScope && before?.kind === "result" ? before.title ?? t("historyWorkspace.chosen") : t("historyWorkspace.notChosen")}</span><span>{t("labels.after")}{" "}{comparisonActorScope === actorScope && after?.kind === "result" ? after.title ?? t("historyWorkspace.chosen") : t("historyWorkspace.notChosen")}</span></div>
      {comparisonActorScope === actorScope && before && after && <DecisionDeltaComparison key={JSON.stringify([actorScope, selectionKey(before), selectionKey(after)])}
        before={before} after={after} actorScope={actorScope} historyCheckEpoch={historyCheckEpoch} suspended={query.isRefetching} />}
    </section>}
    {!contextChanged && !blocked && <div className="history-layout">
      {query.isPending ? <section className="history-timeline"><p role="status">{t("historyWorkspace.loading")}</p></section>
        : query.error && pages.length === 0 ? <section className="history-timeline"><p>{t("historyWorkspace.unknownRecords")}</p></section> : <HistoryTimeline pages={pages} selected={selection}
        showCheckpoints={showCheckpoints} loadingMore={query.isFetchingNextPage} onMore={() => void query.fetchNextPage()} onSelect={row => { setSelection(row.selection); setBefore(null); setAfter(null); }}
        comparison={{ before: comparisonActorScope === actorScope ? before : null, after: comparisonActorScope === actorScope ? after : null, onChoose: (side, row) => chooseComparison(side, row.selection) }} />}
      {selection ? <HistoryDetail key={`${actorScope}:${selectionKey(selection)}`} selection={selection} actorScope={actorScope} onUseQuestion={onUseQuestion}
          onChooseResult={chooseComparison} />
        : <section className="history-detail history-select-prompt"><Icon icon="document-open" size={32} /><h2>{t("historyWorkspace.chooseRecord")}</h2><p>{t("historyWorkspace.compareNotice")}</p><span>{t("historyWorkspace.restoreNotice")}</span></section>}
    </div>}
  </>;
}

export function ResearchHistoryWorkspace({ projectId, threadId, requestDigest, initialSelection, onUseQuestion, compact = false, kinds }: {
  projectId: string; threadId?: string; initialSelection?: HistorySelection;
  onUseQuestion?: (question: string) => void; compact?: boolean; kinds?: HistoryKind[]; requestDigest?: string;
}) {
  const [allProject, setAllProject] = useState(!threadId);
  const scope: HistoryScope = initialSelection?.scope ?? { projectId, ...(!allProject && threadId ? { threadId } : {}), ...(requestDigest ? { requestDigest } : {}) };
  if (scope.projectId !== projectId) return <Callout intent="warning">{t("historyWorkspace.projectMismatch")}</Callout>;
  return <section className={`research-history ${compact ? "history-compact" : ""}`} aria-label={t("workspace.history")}>
    {import.meta.env.VITE_THOTH_TEST_MODE === "true" && <Callout compact intent="warning" className="history-test-banner">{t("historyWorkspace.testMode")}</Callout>}
    <header className="workspace-heading history-heading"><div><p className="eyebrow">{eyebrowLabel("RESEARCH HISTORY")}</p><h1>{kinds?.length === 1 && kinds[0] === "MEMORY" ? t("result.memory") : t("workspace.history")}</h1><p>{t("historyWorkspace.preserve")}</p></div>
      {!initialSelection && !requestDigest && threadId && <div className="history-scope" aria-label={t("historyWorkspace.scope")}><Button small active={!allProject} onClick={() => setAllProject(false)}>{t("historyWorkspace.session")}</Button><Button small active={allProject} onClick={() => setAllProject(true)}>{t("historyWorkspace.project")}</Button></div>}
    </header>
    <ScopedHistory key={JSON.stringify([scope, kinds, initialSelection ? selectionKey(initialSelection) : null])} scope={scope} initialSelection={initialSelection} onUseQuestion={onUseQuestion} kinds={kinds} />
  </section>;
}
