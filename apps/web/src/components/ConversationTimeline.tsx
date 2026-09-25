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

export function ConversationTimeline({ projectId, threadId, status, sourceUris, onDetail, onContinue, onOpenHistory }: {
  projectId: string; threadId: string; status?: ResearchStatus; sourceUris?: string[];
  onDetail: (detail: ResearchDetail) => void;
  onContinue?: () => void;
  onOpenHistory?: () => void;
}) {
  const example = isTimelineExampleRequested();
  const liveStatus = example ? timelineExampleStatus : status;
  const liveSources = example ? TIMELINE_EXAMPLE_SOURCES : sourceUris;
  const protection = useConversationReadProtection(projectId, threadId);
  const query = useInfiniteQuery({
    queryKey: ["conversation", projectId, threadId], enabled: Boolean(threadId) && !example,
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) => readConversation(projectId, threadId, pageParam, signal, protection.block),
    getNextPageParam: page => page.next ?? undefined,
  });
  if (!example && threadId && (protection.blocked || query.data?.pages.some(page => page.redacted) || (query.error && !canRetainConversation(query.error)))) {
    return <section className="conversation-timeline" aria-label="연구 대화"><Callout intent="warning" role="alert">
      대화의 접근 권한이나 저장 기준을 확인하지 못해 답변을 표시하지 않습니다.
      <Button small onClick={() => void query.refetch()}>다시 읽기</Button>
    </Callout></section>;
  }
  if (!example && threadId && !query.data) {
    return <section className="conversation-timeline" aria-label="연구 대화">{query.error
      ? <Callout intent="warning" role="alert">대화를 불러오지 못했습니다. <Button small onClick={() => void query.refetch()}>다시 읽기</Button></Callout>
      : <p className="muted" role="status">대화를 읽는 중…</p>}</section>;
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
  const emptyExistingThread = Boolean(threadId && query.data && turns.length === 0 && !currentQuestion && !running);
  return <section className="conversation-timeline" aria-label="연구 대화">
    {query.hasNextPage && <Button minimal loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>이전 대화 불러오기</Button>}
    {query.error && <Callout intent="warning" role="alert">{query.data ? "대화를 다시 확인하지 못했습니다. 마지막으로 불러온 답변을 표시하며, 최신 상태는 확인되지 않았습니다. " : "대화를 불러오지 못했습니다. "}<Button small onClick={() => void query.refetch()}>다시 읽기</Button></Callout>}
    {query.data?.pages.some(page => page.limited) && <Callout compact>조회 범위를 넘는 과거 이력이 있습니다. 기록에서 확인할 수 있습니다.</Callout>}
    {turns.map(turn => <div className="conversation-turn" key={turn.input.request_revision_digest}>
      <article className="user-message"><header>질문 {turn.input.edit_kind === "REPLACE" ? "수정" : turn.input.edit_kind === "STEER" ? "· 방향 변경" : ""}<time dateTime={turn.input.created_at}>{new Date(turn.input.created_at).toLocaleString()}</time></header><p>{turn.input.text}</p></article>
      <ResearchResultCard result={turn.result} state={turn.state} unavailable={turn.unavailable} error={turn.error} failure={turn.failure} terminalReason={turn.terminalReason} onDetail={onDetail}
        currentness={turn.currentness} historySelection={{kind:"result",scope:{projectId,threadId,requestDigest:turn.input.request_revision_digest},operationId:turn.input.operation_id,
          resultDigest:currentResultDigest(status,{projectId,threadId,requestDigest:turn.input.request_revision_digest},turn.input.operation_id)}} {...followupFor(turn.input.request_revision_digest)} />
    </div>)}
    {unavailableHistory && <Callout compact>이 서버는 과거 대화 조회를 지원하지 않습니다. 확인 가능한 현재 기록만 표시합니다.</Callout>}
    {(currentQuestion || running) && <div className="conversation-turn">
      {currentQuestion && <article className="user-message"><header>질문{example ? " · 예시" : ""}{example ? <time dateTime={TIMELINE_EXAMPLE_ASKED_AT}>{new Date(TIMELINE_EXAMPLE_ASKED_AT).toLocaleString()}</time> : null}</header><p>{currentQuestion}</p></article>}
      <ResearchLiveProgress status={liveStatus} sourceUris={liveSources} />
      {!running && <ResearchResultCard result={currentRequestResult?.result ?? null} state={liveStatus && ["FAILED", "CANCELLED"].includes(liveStatus.operation_state ?? "") ? liveStatus.operation_state! : liveStatus?.current_result ? liveStatus.operation_state ?? "UNKNOWN" : "STALE"} error={liveStatus?.operation_error} failure={liveStatus?.failure} terminalReason={currentRequestResult?.terminal_reason} onDetail={onDetail}
        currentness={currentRequestResult ? liveStatus?.basis_currentness : undefined} historySelection={currentRequestResult?.request_ref?.revision_digest ? {kind:"result",scope:{projectId,threadId,requestDigest:currentRequestResult.request_ref.revision_digest},operationId:currentRequestResult.operation_id,
          resultDigest:currentResultDigest(liveStatus,{projectId,threadId,requestDigest:currentRequestResult.request_ref.revision_digest},currentRequestResult.operation_id)} : undefined} {...followupFor(currentRequestResult?.request_ref?.revision_digest)}/>}
    </div>}
    {emptyExistingThread && <div className="conversation-empty legacy-thread-empty"><Icon icon="chat" size={36}/><h1>이 작업의 대화 기록을 현재 화면에서 불러올 수 없습니다.</h1><p>저장된 연구 이력과 현재 프로젝트 기록을 확인하거나, 같은 작업에서 새 질문을 이어갈 수 있습니다.</p><div className="legacy-thread-actions"><Button intent="primary" icon="edit" onClick={onContinue}>새 질문으로 이어가기</Button><Button icon="history" onClick={onOpenHistory}>연구 이력 확인</Button></div></div>}
    {!threadId && turns.length === 0 && !currentQuestion && !running && <div className="conversation-empty"><span>THOTH</span><h1>지금 무엇이 막혀 있나요?</h1><p>질문과 자료에서 출발해 근거, 가능한 설명, 다음 행동을 함께 확인합니다.</p></div>}
    {query.isPending && threadId && !example && <p className="muted" role="status">대화를 읽는 중…</p>}
  </section>;
}
