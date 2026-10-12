import { message } from "../i18n/formatMessages";
import { getLocale } from "../i18n/locale";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Icon } from "@blueprintjs/core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { readConversation, withCurrentCheckpoint, type ConversationTurn } from "../api/conversation";
import { canRetainConversation } from "../api/conversationRefresh";
import { currentResultDigest } from "../api/resultEvidenceIdentity";
import { useConversationReadProtection } from "./useConversationReadProtection";
import type { ResearchStatus } from "../api/research";
import {
  isTimelineExampleRequested,
  TIMELINE_EXAMPLE_ASKED_AT,
  TIMELINE_EXAMPLE_SOURCES,
  timelineExampleStatus,
} from "../api/timelineExample";
import { ResearchResultCard, type ResearchDetail } from "./ResearchResultCard";
import { ResearchLiveProgress } from "./ResearchLiveProgress";
import { resultUsageLine } from "./resultUsage";
import { useHypothesisLinks } from "./useHypothesisLinks";
import { SyntheticDataNotice } from "./SyntheticDataNotice";

export function ConversationTimeline({ projectId, threadId, status, sourceUris, onDetail, onContinue, onOpenHistory, onOpenTrace, onRetry, onResume, resuming = false, queuedInstruction = false }: {
  projectId: string; threadId: string; status?: ResearchStatus; sourceUris?: string[]; queuedInstruction?: boolean;
  onDetail: (detail: ResearchDetail) => void;
  onContinue?: () => void;
  /** Put the question back in the composer for another run; nothing is sent until the user sends it. */
  onRetry?: (question: string) => void;
  /** Continue the latest interrupted run from where it stopped (the user pressed the button; it starts at once). */
  onResume?: (operationId: string) => void;
  resuming?: boolean;
  onOpenHistory?: () => void;
  /** Back to the trace row an investigation started from. */
  onOpenTrace?: (row: { kind: string; id: string }) => void;
}) {
  const example = isTimelineExampleRequested();
  const liveStatus = example ? timelineExampleStatus : status;
  const liveSources = example ? TIMELINE_EXAMPLE_SOURCES : sourceUris;
  const protection = useConversationReadProtection(projectId, threadId);
  const linked = useHypothesisLinks(projectId, !example);
  useTranslation();
  const query = useInfiniteQuery({
    queryKey: ["conversation", projectId, threadId], enabled: Boolean(threadId) && !example,
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) => readConversation(projectId, threadId, pageParam, signal, protection.block),
    getNextPageParam: page => page.next ?? undefined,
  });
  if (!example && threadId && (protection.blocked || query.data?.pages.some(page => page.redacted) || (query.error && !canRetainConversation(query.error)))) {
    return <section className="conversation-timeline" aria-label={t("timeline.title")}><Callout intent="warning" role="alert">
      {t("timeline.accessUnverified")}
      <Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button>
    </Callout></section>;
  }
  if (!example && threadId && !query.data) {
    return <section className="conversation-timeline" aria-label={t("timeline.title")}>{query.error
      ? <Callout intent="warning" role="alert">{t("timeline.loadFailed")} <Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button></Callout>
      : <p className="muted" role="status">{t("timeline.loading")}</p>}</section>;
  }
  const unique = new Map<string, ConversationTurn>();
  for (const page of query.data?.pages ?? []) for (const turn of page.turns) unique.set(turn.input.request_revision_digest, turn);
  const turns = example ? [] : withCurrentCheckpoint([...unique.values()].sort((a,b) => a.input.request_epoch - b.input.request_epoch), status);
  const unavailableHistory = query.data?.pages[0]?.supported === false;
  const current = liveStatus?.current_result ?? liveStatus?.previous_result;
  const currentRequestResult = !liveStatus?.request || current?.operation_id === liveStatus.request.operation_id ? current : undefined;
  const followupFor = (requestDigest?: string) => requestDigest && liveStatus?.user_progress_summary?.request_revision_digest === requestDigest
    ? { progressSummary: liveStatus.user_progress_summary, coverageMatrix: liveStatus.coverage_matrix, nextUserAction: liveStatus.next_user_action }
    : {};
  const currentQuestion = example || turns.length === 0 ? liveStatus?.request?.authored_text : undefined;
  const running = liveStatus?.operation_state === "RUNNING";
  const finishedStages = (liveStatus?.completed_stages ?? []).filter(stage => stage.state === "COMPLETED").length;
  const resumeOperation = liveStatus?.request?.operation_id;
  const emptyExistingThread = Boolean(threadId && query.data && turns.length === 0 && !currentQuestion && !running);
  return <section className="conversation-timeline" aria-label={t("timeline.title")}>
    {!example && <SyntheticDataNotice projectId={projectId}/>}
    {query.hasNextPage && <Button minimal loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{t("timeline.older")}</Button>}
    {query.error && <Callout intent="warning" role="alert">{query.data ? t("timeline.refreshFailed") : t("timeline.loadFailedSpace")}<Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button></Callout>}
    {query.data?.pages.some(page => page.limited) && <Callout compact>{t("timeline.limited")}</Callout>}
    {turns.map(turn => <div className="conversation-turn" key={turn.input.request_revision_digest}>
      <article className="user-message"><header>{message("questionHeading", turn.input.edit_kind)}<time dateTime={turn.input.created_at}>{new Date(turn.input.created_at).toLocaleString(getLocale() === "en" ? "en-US" : "ko-KR")}</time></header><p>{turn.input.text}</p></article>
      <ResearchResultCard onOpenTrace={onOpenTrace} projectId={projectId} hypothesisLinks={linked.links} linkDistribution={linked.distribution} result={turn.result} state={turn.state} unavailable={turn.unavailable} error={turn.error} failure={turn.failure} terminalReason={turn.terminalReason} onDetail={onDetail} onRetry={onRetry ? () => onRetry(turn.input.text) : undefined} usageLine={resultUsageLine(liveStatus, turn.input.operation_id)}
        currentness={turn.currentness} historySelection={{kind:"result",scope:{projectId,threadId,requestDigest:turn.input.request_revision_digest},operationId:turn.input.operation_id,
          resultDigest:currentResultDigest(status,{projectId,threadId,requestDigest:turn.input.request_revision_digest},turn.input.operation_id)}} {...followupFor(turn.input.request_revision_digest)} />
    </div>)}
    {unavailableHistory && <Callout compact>{t("timeline.unsupported")}</Callout>}
    {(currentQuestion || running) && <div className="conversation-turn">
      {currentQuestion && <article className="user-message"><header>{message("questionExample", example)}{example ? <time dateTime={TIMELINE_EXAMPLE_ASKED_AT}>{new Date(TIMELINE_EXAMPLE_ASKED_AT).toLocaleString(getLocale() === "en" ? "en-US" : "ko-KR")}</time> : null}</header><p>{currentQuestion}</p></article>}
      <ResearchLiveProgress status={liveStatus} sourceUris={liveSources} />
      {queuedInstruction && <Callout compact icon="time" className="queued-instruction-notice" role="status">{t("timeline.queued")}</Callout>}
      {!running && <ResearchResultCard onOpenTrace={onOpenTrace} hypothesisLinks={linked.links} linkDistribution={linked.distribution} result={currentRequestResult?.result ?? null} state={liveStatus && ["FAILED", "CANCELLED"].includes(liveStatus.operation_state ?? "") ? liveStatus.operation_state! : liveStatus?.current_result ? liveStatus.operation_state ?? "UNKNOWN" : "STALE"} error={liveStatus?.operation_error} failure={liveStatus?.failure} terminalReason={currentRequestResult?.terminal_reason} onDetail={onDetail} onRetry={onRetry && liveStatus?.request?.authored_text ? () => onRetry(liveStatus.request!.authored_text!) : undefined}
        onResume={onResume && resumeOperation ? () => onResume(resumeOperation) : undefined} resumeCompleted={finishedStages} resuming={resuming} usageLine={resultUsageLine(liveStatus, currentRequestResult?.operation_id)}
        currentness={currentRequestResult ? liveStatus?.basis_currentness : undefined} executionHold={currentRequestResult ? liveStatus?.execution_hold : undefined} projectId={projectId} holdCauseRef={currentRequestResult?.request_ref?.revision_digest} historySelection={currentRequestResult?.request_ref?.revision_digest ? {kind:"result",scope:{projectId,threadId,requestDigest:currentRequestResult.request_ref.revision_digest},operationId:currentRequestResult.operation_id,
          resultDigest:currentResultDigest(liveStatus,{projectId,threadId,requestDigest:currentRequestResult.request_ref.revision_digest},currentRequestResult.operation_id)} : undefined} {...followupFor(currentRequestResult?.request_ref?.revision_digest)}/>}
    </div>}
    {emptyExistingThread && <div className="conversation-empty legacy-thread-empty"><Icon icon="chat" size={36}/><h1>{t("timeline.unavailable")}</h1><p>{t("timeline.recoverAdvice")}</p><div className="legacy-thread-actions"><Button intent="primary" icon="edit" onClick={onContinue}>{t("timeline.newQuestion")}</Button><Button icon="history" onClick={onOpenHistory}>{t("timeline.openHistory")}</Button></div></div>}
    {!threadId && turns.length === 0 && !currentQuestion && !running && <div className="conversation-empty"><span>THOTH</span><h1>{t("timeline.emptyTitle")}</h1><p>{t("timeline.emptyNotice")}</p></div>}
    {query.isPending && threadId && !example && <p className="muted" role="status">{t("timeline.loading")}</p>}
  </section>;
}
