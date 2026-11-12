import { t } from "../../i18n/locale";
import { Button, Callout, Icon, Switch } from "@blueprintjs/core";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { readHistoryPage } from "../../api/history";
import { RpcError } from "../../api/rpcClient";
import {
  selectionKey,
  type HistoryKind,
  type HistoryScope,
  type HistorySelection,
} from "../../api/historyModels";
import { HistoryDetail } from "./HistoryDetail";
import { HistoryTimeline } from "./HistoryTimeline";
import { Disclosure } from "../Disclosure";
import { ProjectReviewEmptyNote, ProjectReviewList } from "./ProjectReviewList";
import { DecisionDeltaComparison } from "./DecisionDeltaComparison";
import { useReadAccessProtection } from "../useReadAccessProtection";

/** The history pages of one scope, read page by page; an access refusal empties them. */
function useHistoryPages(scope: HistoryScope, kinds?: HistoryKind[]) {
  const queryKey = [
    "research-history",
    scope.projectId,
    scope.threadId ?? null,
    scope.requestDigest ?? null,
    kinds?.join(",") ?? "all",
  ];
  const query = useInfiniteQuery({
    queryKey,
    initialPageParam: null as string | null,
    retry: false,
    gcTime: 0,
    queryFn: ({ pageParam, signal }) => readHistoryPage(scope, pageParam, signal, kinds),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
  const blocked = useReadAccessProtection(queryKey, query.error);
  const pages = blocked ? [] : (query.data?.pages ?? []);
  return { queryKey, query, blocked, pages, actorScope: pages[0]?.actorScope ?? null };
}

function HistoryReadFailure({
  error,
  blocked,
  onReload,
}: {
  error: Error | null;
  blocked: boolean;
  onReload: () => void;
}) {
  if (!error && !blocked) return null;
  const invalidCursor =
    error instanceof RpcError && error.details.reason_code === "HISTORY_CURSOR_INVALID";
  return (
    <Callout intent="warning" role="alert">
      {invalidCursor ? t("historyWorkspace.cursorExpired") : t("historyWorkspace.failed")}
      <Button small onClick={onReload}>
        {t("workspace.reload")}
      </Button>
      <Disclosure defaultOpen label={t("historyDetail.diagnostics")}>
        <p>{error?.message}</p>
      </Disclosure>
    </Callout>
  );
}

/**
 * The panel next to an answer: that answer's record only, with one way to the whole history. The panel is
 * 400px wide; the whole history screen in it read ten letters to a line.
 */
function AnswerRecord({
  scope,
  selection,
  onUseQuestion,
  onOpenFull,
}: {
  scope: HistoryScope;
  selection: HistorySelection;
  onUseQuestion?: (question: string) => void;
  onOpenFull?: () => void;
}) {
  const client = useQueryClient();
  const { queryKey, query, blocked, actorScope } = useHistoryPages(scope);
  return (
    <>
      <HistoryReadFailure
        error={query.error}
        blocked={blocked}
        onReload={() => void client.resetQueries({ queryKey, exact: true })}
      />
      {!blocked && (
        <HistoryDetail
          key={`${actorScope}:${selectionKey(selection)}`}
          selection={selection}
          actorScope={actorScope}
          onUseQuestion={onUseQuestion}
        />
      )}
      {onOpenFull && (
        <Button minimal icon="history" className="history-open-full" onClick={onOpenFull}>
          {t("sidePanel.openHistory")}
        </Button>
      )}
    </>
  );
}

function ScopedHistory({
  scope,
  initialSelection,
  onUseQuestion,
  kinds,
}: {
  scope: HistoryScope;
  initialSelection?: HistorySelection;
  onUseQuestion?: (question: string) => void;
  kinds?: HistoryKind[];
}) {
  const client = useQueryClient();
  const [selection, setSelection] = useState<HistorySelection | null>(initialSelection ?? null);
  const [before, setBefore] = useState<HistorySelection | null>(null);
  const [after, setAfter] = useState<HistorySelection | null>(null);
  const [comparisonActorScope, setComparisonActorScope] = useState<string | null>(null);
  const [historyCheckEpoch, setHistoryCheckEpoch] = useState(0);
  const previousHistory = useRef({ updatedAt: 0, pageCount: 0 });
  const [showCheckpoints, setShowCheckpoints] = useState(false);
  const { queryKey, query, blocked, pages, actorScope } = useHistoryPages(scope, kinds);
  const contextChanged = pages.some((page) => page.actorScope !== actorScope);
  const historyError = Boolean(query.error);
  useEffect(() => {
    setBefore(null);
    setAfter(null);
    setComparisonActorScope(null);
  }, [actorScope, contextChanged, historyError]);
  useEffect(() => {
    const previous = previousHistory.current;
    if (
      previous.updatedAt > 0 &&
      query.dataUpdatedAt > 0 &&
      query.dataUpdatedAt !== previous.updatedAt &&
      pages.length <= previous.pageCount
    ) {
      setHistoryCheckEpoch((value) => value + 1);
    }
    previousHistory.current = { updatedAt: query.dataUpdatedAt, pageCount: pages.length };
  }, [query.dataUpdatedAt, pages.length]);
  const refresh = () => {
    setSelection(null);
    setBefore(null);
    setAfter(null);
    setComparisonActorScope(null);
    void client.resetQueries({ queryKey, exact: true });
    // One "read again" for the whole screen: the answers that need review are read again with the records.
    void client.resetQueries({ queryKey: ["project-review-list", scope.projectId], exact: true });
  };
  const chooseComparison = (side: "before" | "after", next: HistorySelection) => {
    if (!actorScope || next.kind !== "result" || !next.resultDigest) return;
    setComparisonActorScope(actorScope);
    if (side === "before") setBefore(next);
    else setAfter(next);
    setSelection(next);
  };
  const comparing = Boolean(before || after);
  const showReview = !initialSelection && !kinds;
  return (
    <>
      <div className="history-toolbar">
        <ProjectReviewEmptyNote projectId={scope.projectId} show={showReview} />
        <div className="history-toolbar-actions">
          <Switch
            label={t("historyWorkspace.checkpoints")}
            checked={showCheckpoints}
            onChange={(event) => setShowCheckpoints(event.currentTarget.checked)}
          />
          <Button minimal icon="refresh" onClick={refresh} loading={query.isRefetching}>
            {t("historyWorkspace.reload")}
          </Button>
        </div>
      </div>
      <HistoryReadFailure error={query.error} blocked={blocked} onReload={refresh} />
      {contextChanged && (
        <Callout intent="warning" role="alert">
          {t("historyWorkspace.scopeChanged")}
          <Button small onClick={refresh}>
            {t("historyWorkspace.reload")}
          </Button>
        </Callout>
      )}
      {showReview && (
        <ProjectReviewList
          projectId={scope.projectId}
          onSelect={(next) => {
            setSelection(next);
            setBefore(null);
            setAfter(null);
          }}
        />
      )}
      {/* Shown once a side has been chosen in the list. Before that it was an empty card above the records. */}
      {comparing && !contextChanged && !query.error && pages.length > 0 && (
        <section
          className="history-decision-comparison"
          aria-label={t("historyWorkspace.selection")}
        >
          <div className="section-title-row">
            <div>
              <h2>{t("historyWorkspace.compare")}</h2>
              <p>{t("historyWorkspace.choosePair")}</p>
            </div>
            <Button
              small
              minimal
              onClick={() => {
                setBefore(null);
                setAfter(null);
              }}
            >
              {t("historyWorkspace.clearPair")}
            </Button>
          </div>
          <div className="history-compare-selected">
            <span>
              {t("labels.before")}{" "}
              {comparisonActorScope === actorScope && before?.kind === "result"
                ? (before.title ?? t("historyWorkspace.chosen"))
                : t("historyWorkspace.notChosen")}
            </span>
            <span>
              {t("labels.after")}{" "}
              {comparisonActorScope === actorScope && after?.kind === "result"
                ? (after.title ?? t("historyWorkspace.chosen"))
                : t("historyWorkspace.notChosen")}
            </span>
          </div>
          {comparisonActorScope === actorScope && before && after && (
            <DecisionDeltaComparison
              key={JSON.stringify([actorScope, selectionKey(before), selectionKey(after)])}
              before={before}
              after={after}
              actorScope={actorScope}
              historyCheckEpoch={historyCheckEpoch}
              suspended={query.isRefetching}
            />
          )}
        </section>
      )}
      {!contextChanged && !blocked && (
        <div className="history-layout">
          {query.isPending ? (
            <section className="history-timeline">
              <p role="status">{t("historyWorkspace.loading")}</p>
            </section>
          ) : query.error && pages.length === 0 ? (
            <section className="history-timeline">
              <p>{t("historyWorkspace.unknownRecords")}</p>
            </section>
          ) : (
            <HistoryTimeline
              pages={pages}
              selected={selection}
              showCheckpoints={showCheckpoints}
              loadingMore={query.isFetchingNextPage}
              onMore={() => void query.fetchNextPage()}
              onSelect={(row) => {
                setSelection(row.selection);
                setBefore(null);
                setAfter(null);
              }}
              comparison={{
                before: comparisonActorScope === actorScope ? before : null,
                after: comparisonActorScope === actorScope ? after : null,
                onChoose: (side, row) => chooseComparison(side, row.selection),
              }}
            />
          )}
          {selection ? (
            <HistoryDetail
              key={`${actorScope}:${selectionKey(selection)}`}
              selection={selection}
              actorScope={actorScope}
              onUseQuestion={onUseQuestion}
              onChooseResult={chooseComparison}
            />
          ) : (
            <section className="history-detail history-select-prompt">
              <Icon icon="document-open" size={32} />
              <h2>{t("historyWorkspace.chooseRecord")}</h2>
              <p>{t("historyWorkspace.compareNotice")}</p>
              <span>{t("historyWorkspace.restoreNotice")}</span>
            </section>
          )}
        </div>
      )}
    </>
  );
}

export function ResearchHistoryWorkspace({
  projectId,
  threadId,
  requestDigest,
  initialSelection,
  onUseQuestion,
  compact = false,
  kinds,
  onOpenFull,
}: {
  projectId: string;
  threadId?: string;
  initialSelection?: HistorySelection;
  onUseQuestion?: (question: string) => void;
  compact?: boolean;
  kinds?: HistoryKind[];
  requestDigest?: string;
  /** compact: the panel next to an answer, which shows that answer's record only. onOpenFull opens the history tab from there. */
  onOpenFull?: () => void;
}) {
  const [allProject, setAllProject] = useState(!threadId);
  const scope: HistoryScope = initialSelection?.scope ?? {
    projectId,
    ...(!allProject && threadId ? { threadId } : {}),
    ...(requestDigest ? { requestDigest } : {}),
  };
  if (scope.projectId !== projectId)
    return <Callout intent="warning">{t("historyWorkspace.projectMismatch")}</Callout>;
  return (
    <section
      className={`research-history ${compact ? "history-compact" : ""}`}
      aria-label={t("workspace.history")}
    >
      {import.meta.env.VITE_THOTH_TEST_MODE === "true" && (
        <Callout compact intent="warning" className="history-test-banner">
          {t("historyWorkspace.testMode")}
        </Callout>
      )}
      {/* The panel has its own title ("변경 이력"); a second, different one inside it read as another feature. */}
      {!compact && (
        <header className="workspace-heading history-heading">
          <div>
            <h1>
              {kinds?.length === 1 && kinds[0] === "MEMORY"
                ? t("result.memory")
                : t("workspace.history")}
            </h1>
            <p>{t("historyWorkspace.preserve")}</p>
          </div>
          {!initialSelection && !requestDigest && threadId && (
            <div className="history-scope" role="group" aria-label={t("historyWorkspace.scope")}>
              <Button small active={!allProject} onClick={() => setAllProject(false)}>
                {t("historyWorkspace.session")}
              </Button>
              <Button small active={allProject} onClick={() => setAllProject(true)}>
                {t("historyWorkspace.project")}
              </Button>
            </div>
          )}
        </header>
      )}
      {compact && initialSelection ? (
        <AnswerRecord
          key={selectionKey(initialSelection)}
          scope={scope}
          selection={initialSelection}
          onUseQuestion={onUseQuestion}
          onOpenFull={onOpenFull}
        />
      ) : (
        <ScopedHistory
          key={JSON.stringify([
            scope,
            kinds,
            initialSelection ? selectionKey(initialSelection) : null,
          ])}
          scope={scope}
          initialSelection={initialSelection}
          onUseQuestion={onUseQuestion}
          kinds={kinds}
        />
      )}
    </section>
  );
}
