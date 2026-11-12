import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Icon } from "@blueprintjs/core";
import type { ReactNode } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { readConversation, type ConversationTurn } from "../api/conversation";
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
import { useAutoRetryOff } from "./useAutoRetryOff";
import { SyntheticDataNotice } from "./SyntheticDataNotice";
import { ConversationTurnView, QuestionBubble } from "./ConversationTurnView";
import { collectTurns, savedBeforeSet, turnTally } from "./timelineTurns";
import { useOpenAtLatestTurn } from "./useOpenAtLatestTurn";

type TimelineProps = {
  projectId: string; threadId: string; status?: ResearchStatus; sourceUris?: string[]; queuedInstruction?: boolean;
  onDetail: (detail: ResearchDetail) => void;
  onContinue?: () => void;
  /** Put the question back in the composer for another run; nothing is sent until the user sends it. */
  onRetry?: (question: string) => void;
  /** Continue the latest interrupted run from where it stopped (the user pressed the button; it starts at once). */
  onResume?: (operationId: string) => void;
  resuming?: boolean;
  onOpenHistory?: () => void;
  /** Open the project's Settings page (where automatic retry is switched). */
  onOpenSettings?: () => void;
  /** Back to the trace row an investigation started from. */
  onOpenTrace?: (row: { kind: string; id: string }) => void;
  /** Shown under the empty-thread greeting: what the project offers before the first question. */
  emptyNotice?: ReactNode;
};

/** What a result card needs to say that automatic retry is off. */
type RetryNotice = { autoRetryOff: boolean; onOpenSettings?: () => void };

/** The follow-up fields of the live status belong to exactly one request revision. */
function followupFor(liveStatus: ResearchStatus | undefined, requestDigest?: string) {
  return requestDigest && liveStatus?.user_progress_summary?.request_revision_digest === requestDigest
    ? { progressSummary: liveStatus.user_progress_summary, coverageMatrix: liveStatus.coverage_matrix, nextUserAction: liveStatus.next_user_action }
    : {};
}
/** A saved round the live status does not describe: the answer checks are sent for the latest request only. */
function isEarlierRound(liveStatus: ResearchStatus | undefined, requestDigest: string) {
  const latest = liveStatus?.user_progress_summary?.request_revision_digest;
  return Boolean(latest) && latest !== requestDigest;
}
/** The saved turn whose research the live status says is running (or about to run) now. */
function isRunningTurn(liveStatus: ResearchStatus | undefined, operationId: string) {
  return ["PENDING", "RUNNING"].includes(liveStatus?.operation_state ?? "") && liveStatus?.request?.operation_id === operationId;
}

/** The request the live status is about, when it has produced or kept a checkpoint; otherwise the live block shows progress only. */
function currentRequestOf(liveStatus: ResearchStatus | undefined) {
  const current = liveStatus?.current_result ?? liveStatus?.previous_result;
  return !liveStatus?.request || current?.operation_id === liveStatus.request.operation_id ? current : undefined;
}

/** The card state of the current request: a failed or cancelled operation keeps that state; a kept checkpoint uses the live state; otherwise it is stale. */
function currentCardState(liveStatus: ResearchStatus | undefined): string {
  if (liveStatus && ["FAILED", "CANCELLED"].includes(liveStatus.operation_state ?? "")) return liveStatus.operation_state!;
  return liveStatus?.current_result ? liveStatus.operation_state ?? "UNKNOWN" : "STALE";
}

/** The fields of the current request's result card that come from the live status and its checkpoint. */
function currentCardProps(liveStatus: ResearchStatus | undefined, projectId: string, threadId: string) {
  const checkpoint = currentRequestOf(liveStatus);
  const requestDigest = checkpoint?.request_ref?.revision_digest;
  const scope = { projectId, threadId, requestDigest: requestDigest ?? "" };
  return {
    result: checkpoint?.result ?? null, state: currentCardState(liveStatus),
    error: liveStatus?.operation_error, failure: liveStatus?.failure, terminalReason: checkpoint?.terminal_reason,
    hasSavedResult: Boolean(liveStatus?.previous_result),
    resumeCompleted: (liveStatus?.completed_stages ?? []).filter(stage => stage.state === "COMPLETED").length,
    usageLine: resultUsageLine(liveStatus, checkpoint?.operation_id),
    currentness: checkpoint ? liveStatus?.basis_currentness : undefined, executionHold: checkpoint ? liveStatus?.execution_hold : undefined, holdCauseRef: requestDigest,
    historySelection: requestDigest && checkpoint ? { kind: "result" as const, scope, operationId: checkpoint.operation_id, resultDigest: currentResultDigest(liveStatus, scope, checkpoint.operation_id) } : undefined,
    ...followupFor(liveStatus, requestDigest),
  };
}

/** The current request: its question (when no saved turn carries it yet), live progress, and the card of what it produced. */
function CurrentTurn({ projectId, threadId, liveStatus, liveSources, example, question, running, onDetail, onRetry, onResume, resuming, queuedInstruction, onOpenTrace, linked, retry }: {
  retry: RetryNotice;
  projectId: string; threadId: string; liveStatus?: ResearchStatus; liveSources?: string[]; example: boolean; question?: string; running: boolean;
  onDetail: TimelineProps["onDetail"]; onRetry?: TimelineProps["onRetry"]; onResume?: TimelineProps["onResume"]; resuming: boolean; queuedInstruction: boolean;
  onOpenTrace?: TimelineProps["onOpenTrace"]; linked: ReturnType<typeof useHypothesisLinks>;
}) {
  const authored = liveStatus?.request?.authored_text;
  const resumeOperation = liveStatus?.request?.operation_id;
  return <div className="conversation-turn conversation-turn-current">
    {question && <QuestionBubble heading={message("questionExample", example)} time={example ? TIMELINE_EXAMPLE_ASKED_AT : null} text={question}/>}
    <ResearchLiveProgress status={liveStatus} sourceUris={liveSources} />
    {queuedInstruction && <Callout compact icon="time" className="queued-instruction-notice" role="status">{t("timeline.queued")}</Callout>}
    {!running && <ResearchResultCard {...currentCardProps(liveStatus, projectId, threadId)} {...retry} onOpenTrace={onOpenTrace} hypothesisLinks={linked.links} linkDistribution={linked.distribution} projectId={projectId} onDetail={onDetail}
      onRetry={onRetry && authored ? () => onRetry(authored) : undefined}
      onResume={onResume && resumeOperation ? () => onResume(resumeOperation) : undefined} resuming={resuming}/>}
  </div>;
}

/** A saved turn's full result card, with the live follow-up fields when they belong to this request. */
function SavedTurnCard({ turn, projectId, threadId, status, liveStatus, savedBefore, onDetail, onRetry, onOpenTrace, linked, retry }: {
  retry: RetryNotice;
  turn: ConversationTurn; projectId: string; threadId: string; status?: ResearchStatus; liveStatus?: ResearchStatus; savedBefore: Set<string>;
  onDetail: TimelineProps["onDetail"]; onRetry?: TimelineProps["onRetry"]; onOpenTrace?: TimelineProps["onOpenTrace"]; linked: ReturnType<typeof useHypothesisLinks>;
}) {
  const requestDigest = turn.input.request_revision_digest;
  const earlier = isEarlierRound(liveStatus, requestDigest);
  // The server sends the usage of the latest result and of the request now running only. An earlier round has
  // none here, which is not the same as "no usage recorded": its line is left out (the conversation's total is
  // under the question box).
  const usageLine = earlier && !liveStatus?.result_usage?.[turn.input.operation_id] ? undefined : resultUsageLine(liveStatus, turn.input.operation_id);
  return <ResearchResultCard {...retry} onOpenTrace={onOpenTrace} projectId={projectId} hypothesisLinks={linked.links} linkDistribution={linked.distribution} result={turn.result} state={turn.state} unavailable={turn.unavailable} error={turn.error} failure={turn.failure} terminalReason={turn.terminalReason} onDetail={onDetail} onRetry={onRetry ? () => onRetry(turn.input.text) : undefined} usageLine={usageLine}
    hasSavedResult={savedBefore.has(requestDigest) || Boolean(liveStatus?.request?.operation_id === turn.input.operation_id && liveStatus.previous_result)}
    currentness={turn.currentness} historySelection={{ kind: "result", scope: { projectId, threadId, requestDigest }, operationId: turn.input.operation_id,
      resultDigest: currentResultDigest(status, { projectId, threadId, requestDigest }, turn.input.operation_id) }} {...followupFor(liveStatus, requestDigest)} earlierRound={earlier}/>;
}

export function ConversationTimeline(props: TimelineProps) {
  const { projectId, threadId, status, sourceUris, onDetail, onContinue, onOpenHistory, onOpenTrace, onOpenSettings, onRetry, onResume, resuming = false, queuedInstruction = false, emptyNotice } = props;
  const example = isTimelineExampleRequested();
  const retry: RetryNotice = { autoRetryOff: useAutoRetryOff(projectId, !example), onOpenSettings };
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
  const timelineRef = useOpenAtLatestTurn(threadId, Boolean(query.data));
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
  const turns = example ? [] : collectTurns(query.data?.pages, status);
  const savedBefore = savedBeforeSet(turns);
  const tally = turnTally(turns);
  const unavailableHistory = query.data?.pages[0]?.supported === false;
  const currentQuestion = example || turns.length === 0 ? liveStatus?.request?.authored_text : undefined;
  const running = liveStatus?.operation_state === "RUNNING";
  const emptyExistingThread = Boolean(threadId && query.data && turns.length === 0 && !currentQuestion && !running);
  // The newest saved turn stays open unless a live block follows it; earlier turns start folded.
  const openIndex = currentQuestion || running ? -1 : turns.length - 1;
  return <section ref={timelineRef} className="conversation-timeline" aria-label={t("timeline.title")}>
    {!example && <SyntheticDataNotice projectId={projectId}/>}
    {turns.length > 1 && <p className="turn-tally muted" role="status">{message("turnTally", tally.total, tally.answered, tally.interrupted)}</p>}
    {query.hasNextPage && <Button minimal loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{t("timeline.older")}</Button>}
    {query.error && <Callout intent="warning" role="alert">{query.data ? t("timeline.refreshFailed") : t("timeline.loadFailedSpace")}<Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button></Callout>}
    {query.data?.pages.some(page => page.limited) && <Callout compact>{t("timeline.limited")}</Callout>}
    {turns.map((turn, index) => <ConversationTurnView key={turn.input.request_revision_digest} turn={turn} index={index + 1} expanded={index === openIndex} onRetry={onRetry ? () => onRetry(turn.input.text) : undefined}
      inProgress={isRunningTurn(liveStatus, turn.input.operation_id)}>
      <SavedTurnCard turn={turn} projectId={projectId} threadId={threadId} status={status} liveStatus={liveStatus} savedBefore={savedBefore} onDetail={onDetail} onRetry={onRetry} onOpenTrace={onOpenTrace} linked={linked} retry={retry}/>
    </ConversationTurnView>)}
    {unavailableHistory && <Callout compact>{t("timeline.unsupported")}</Callout>}
    {(currentQuestion || running) && <CurrentTurn projectId={projectId} threadId={threadId} liveStatus={liveStatus} liveSources={liveSources} example={example} question={currentQuestion} running={running}
      onDetail={onDetail} onRetry={onRetry} onResume={onResume} resuming={resuming} queuedInstruction={queuedInstruction} onOpenTrace={onOpenTrace} linked={linked} retry={retry}/>}
    {emptyExistingThread && <div className="conversation-empty legacy-thread-empty"><Icon icon="chat" size={36}/><h1>{t("timeline.unavailable")}</h1><p>{t("timeline.recoverAdvice")}</p><div className="legacy-thread-actions"><Button intent="primary" icon="edit" onClick={onContinue}>{t("timeline.newQuestion")}</Button><Button icon="history" onClick={onOpenHistory}>{t("timeline.openHistory")}</Button></div></div>}
    {!threadId && turns.length === 0 && !currentQuestion && !running && <div className="conversation-empty"><span>THOTH</span><h1>{t("timeline.emptyTitle")}</h1><p>{t("timeline.emptyNotice")}</p>{emptyNotice}</div>}
    {query.isPending && threadId && !example && <p className="muted" role="status">{t("timeline.loading")}</p>}
  </section>;
}
