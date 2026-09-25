import { Button, Callout, Tag } from "@blueprintjs/core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { readProjectReviewList, type ProjectReviewItem } from "../../api/researchFollowup";
import type { HistorySelection } from "../../api/historyModels";
import { currentnessLabel, currentnessTone } from "./historyPresentation";

function priorityLabel(priority: ProjectReviewItem["priority"]) {
  if (priority === "HIGH") return "높음";
  if (priority === "MEDIUM") return "보통";
  return "낮음";
}

function selectionOf(item: ProjectReviewItem): HistorySelection {
  return { kind: "result", scope: { projectId: item.project_id, threadId: item.thread_id, requestDigest: item.request_revision_digest },
    resultDigest: item.result_revision_digest ?? undefined, title: item.title };
}

export function ProjectReviewList({ projectId, onSelect }: { projectId: string; onSelect: (selection: HistorySelection) => void }) {
  const query = useInfiniteQuery({ queryKey: ["project-review-list", projectId], initialPageParam: null as string | null, retry: false, gcTime: 0,
    queryFn: ({ pageParam, signal }) => readProjectReviewList(projectId, pageParam, signal), getNextPageParam: page => page.next_cursor ?? undefined });
  const pages = query.data?.pages ?? [];
  const items = pages.flatMap(page => page.items);
  if (query.error && pages.length === 0) return <Callout compact intent="warning" role="alert">검토 필요 목록을 읽지 못했습니다. 연구 이력은 계속 볼 수 있습니다.<Button small onClick={() => void query.refetch()}>다시 읽기</Button></Callout>;
  return <section className="project-review-list" aria-label="검토 필요 답변">
    <div className="section-title-row"><div><h2>검토 필요</h2><p>서버가 현재 기준 재확인이나 gap 검토가 필요하다고 표시한 답변입니다.</p></div><Button small minimal icon="refresh" onClick={() => void query.refetch()} loading={query.isRefetching}>새로 읽기</Button></div>
    {query.isPending ? <p className="muted" role="status">검토 목록을 읽는 중…</p> : items.length === 0 ? <p className="muted">현재 프로젝트에 표시할 검토 필요 답변이 없습니다.</p>
      : <div className="review-items">{items.map(item => <article className="review-item" key={item.item_id}>
        <div><Tag minimal>{priorityLabel(item.priority)}</Tag><Tag minimal intent={currentnessTone(item.currentness)}>{currentnessLabel(item.currentness)}</Tag></div>
        <h3>{item.title}</h3>
        <p>{item.next_user_action.label}</p>
        {item.reason_codes.length > 0 && <small>{item.reason_codes.slice(0, 3).join(" · ")}</small>}
        <Button small icon="document-open" onClick={() => onSelect(selectionOf(item))}>해당 답변 열기</Button>
      </article>)}</div>}
    {query.error && pages.length > 0 && <Callout compact intent="warning">검토 목록의 다음 구간을 읽지 못했습니다.</Callout>}
    {query.hasNextPage && <Button small onClick={() => void query.fetchNextPage()} loading={query.isFetchingNextPage}>다음 검토 항목 보기</Button>}
    {pages[0] && (!pages[0].unread_supported || !pages[0].assignment_supported) && <p className="muted">읽지 않음, 배정, 댓글 알림은 이 목록의 계약에 포함되지 않습니다.</p>}
  </section>;
}
