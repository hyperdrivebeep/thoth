import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { readResultEvidence, resultContentKey, type ResultSelection, type EvidencePagePosition } from "../api/resultEvidence";
import { selectionKey } from "../api/historyModels";
import { EvidenceRail } from "./EvidenceRail";
import { useEffect, useMemo } from "react";
import { consistentEvidencePages, useResultEvidenceProtection } from "./useResultEvidenceProtection";
import { Disclosure } from "./Disclosure";

export function ResultEvidencePanel({ selection, result, focusSpanId, citations }: {
  selection: ResultSelection; result: Record<string, unknown>; focusSpanId?: string; citations?: Record<string, number>;
}) {
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
  const loadedIds = (query.data?.pages ?? []).flatMap(page => page.items.map(item => item.span_id));
  const focusLoaded = !focusSpanId || loadedIds.includes(focusSpanId);
  const { hasNextPage, isFetching, isFetchingNextPage, fetchNextPage } = query;
  useEffect(() => {
    // A cited source may sit on a later page: keep reading pages until it is found or the list ends.
    if (!focusLoaded && hasNextPage && !isFetching && !protection.blocked) void fetchNextPage();
  }, [focusLoaded, hasNextPage, isFetching, protection.blocked, fetchNextPage, loadedIds.length]);
  if (query.isPending || (query.isFetching && !isFetchingNextPage)) return <p role="status">{t("evidence.loading")}</p>;
  if (protection.reason === "SCOPE_CHANGED") return <Callout intent="warning" role="alert">{t("evidence.scopeChanged")}</Callout>;
  if (query.error || protection.blocked || !query.data || query.data.pages.length === 0) return <Callout intent="warning" role="alert">{t("evidence.failed")}<Button small onClick={() => void query.refetch()}>{t("evidence.reload")}</Button><Disclosure defaultOpen label={t("historyDetail.diagnostics")}><p>{query.error?.message}</p></Disclosure></Callout>;
  const pages = query.data.pages;
  const first = pages[0];
  if (!consistentEvidencePages(pages)) {
    return <Callout intent="warning" role="alert">{t("evidence.scopeChanged")}</Callout>;
  }
  const items = pages.flatMap(page => page.items);
  const available = items.flatMap(item => item.span ? [item.span] : []);
  const unavailable = items.filter(item => item.availability !== "AVAILABLE");
  return <section aria-label={t("evidence.selected")}>
    <h3>{formatMessage("answerSelectedEvidence", first.selected_count)}</h3>
    <p>{t("evidence.selectionNotice")}</p>
    {first.selected_count === 0 ? <p>{t("evidence.empty")}</p> : <>
      <p className="muted">{t("evidence.countNotice")}</p>
      {focusSpanId && !focusLoaded && !query.hasNextPage && <p className="result-notice" role="status">{t("evidence.citationMissing")}</p>}
      {focusSpanId && unavailable.some(item => item.span_id === focusSpanId) && <p className="result-notice" role="status">{t("evidence.unavailable")}</p>}
      {available.length > 0 && <EvidenceRail evidence={available} heading={formatMessage("availableSources", available.length)} focusSpanId={focusSpanId} citations={citations}/>}
      {unavailable.map(item => <p className="result-notice" key={item.span_id}>{item.availability === "BASIS_MISMATCH" ? t("evidence.mismatch")
        : item.availability === "UNKNOWN_BASIS" ? t("evidence.unknownBasis") : t("evidence.unreadable")}<br/><small>{item.span_id}</small></p>)}
      <p>{formatMessage("evidenceReadCounts", first.selected_count, items.length, available.length, unavailable.length)}</p>
      {query.hasNextPage && <Button onClick={() => void query.fetchNextPage()}>{t("evidence.next")}</Button>}
    </>}
  </section>;
}
