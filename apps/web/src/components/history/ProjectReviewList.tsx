import { t } from "../../i18n/locale";
import { Button, Callout, Tag } from "@blueprintjs/core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { readProjectReviewList, type ProjectReviewItem } from "../../api/researchFollowup";
import type { HistorySelection } from "../../api/historyModels";
import { currentnessLabel, currentnessTone } from "./historyPresentation";
import { nextActionLabel } from "../statusLabels";
import { describeHistoryReasons } from "./historyReasonText";
import { useReadAccessProtection } from "../useReadAccessProtection";

function priorityLabel(priority: ProjectReviewItem["priority"]) {
  if (priority === "HIGH") return t("state.high");
  if (priority === "MEDIUM") return t("state.mediumTime");
  return t("state.low");
}

function selectionOf(item: ProjectReviewItem): HistorySelection {
  return {
    kind: "result",
    scope: {
      projectId: item.project_id,
      threadId: item.thread_id,
      requestDigest: item.request_revision_digest,
    },
    resultDigest: item.result_revision_digest ?? undefined,
    title: item.title,
  };
}

/** The answers of a project that need review. Both pieces below read it; the request is made once. */
function useProjectReview(projectId: string, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: ["project-review-list", projectId],
    initialPageParam: null as string | null,
    retry: false,
    gcTime: 0,
    enabled,
    queryFn: ({ pageParam, signal }) => readProjectReviewList(projectId, pageParam, signal),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const blocked = useReadAccessProtection(["project-review-list", projectId], query.error);
  const pages = blocked ? [] : (query.data?.pages ?? []);
  const items = pages.flatMap((page) => page.items);
  return {
    query,
    blocked,
    pages,
    items,
    nothingToReview: !query.isPending && !query.error && items.length === 0,
  };
}

/**
 * Nothing to review is one line next to the screen's controls, not a card above the records. Where the
 * screen shows no review list (show is false) the place stays empty and nothing is read.
 */
export function ProjectReviewEmptyNote({ projectId, show }: { projectId: string; show: boolean }) {
  const { nothingToReview } = useProjectReview(projectId, show);
  return show && nothingToReview ? (
    <p className="muted project-review-empty">{t("reviewList.empty")}</p>
  ) : (
    <span />
  );
}

export function ProjectReviewList({
  projectId,
  onSelect,
}: {
  projectId: string;
  onSelect: (selection: HistorySelection) => void;
}) {
  const { query, blocked, pages, items, nothingToReview } = useProjectReview(projectId);
  if ((query.error || blocked) && pages.length === 0)
    return (
      <Callout compact intent="warning" role="alert">
        {t("reviewList.failed")}
        <Button small onClick={() => void query.refetch()}>
          {t("workspace.reload")}
        </Button>
      </Callout>
    );
  // The empty state is ProjectReviewEmptyNote. The screen's own "read again" reads this list too.
  if (nothingToReview) return null;
  return (
    <section className="project-review-list" aria-label={t("reviewList.title")}>
      <div className="section-title-row">
        <div>
          <h2>{t("followup.reviewNeeded")}</h2>
          <p>{t("reviewList.notice")}</p>
        </div>
      </div>
      {query.isPending ? (
        <p className="muted" role="status">
          {t("reviewList.loading")}
        </p>
      ) : (
        <div className="review-items">
          {items.map((item) => (
            <article className="review-item" key={item.item_id}>
              <div>
                <Tag minimal>{priorityLabel(item.priority)}</Tag>
                <Tag minimal intent={currentnessTone(item.currentness)}>
                  {currentnessLabel(item.currentness)}
                </Tag>
              </div>
              <h3>{item.title}</h3>
              <p>{nextActionLabel(item.next_user_action)}</p>
              {item.reason_codes.length > 0 && (
                <ul className="review-reasons">
                  {describeHistoryReasons(item.reason_codes)
                    .slice(0, 3)
                    .map((line) => (
                      <li key={line}>
                        <small>{line}</small>
                      </li>
                    ))}
                </ul>
              )}
              <details className="connection-tech">
                <summary>{t("oauth.technical")}</summary>
                <small>{item.next_user_action.label}</small>
                {item.reason_codes.map((code) => (
                  <small key={code}>{code}</small>
                ))}
              </details>
              <Button small icon="document-open" onClick={() => onSelect(selectionOf(item))}>
                {t("reviewList.open")}
              </Button>
            </article>
          ))}
        </div>
      )}
      {query.error && pages.length > 0 && (
        <Callout compact intent="warning" role="alert">
          {t("reviewList.pageFailed")}
        </Callout>
      )}
      {query.hasNextPage && (
        <Button small onClick={() => void query.fetchNextPage()} loading={query.isFetchingNextPage}>
          {t("reviewList.next")}
        </Button>
      )}
      {pages[0] && (!pages[0].unread_supported || !pages[0].assignment_supported) && (
        <p className="muted">{t("reviewList.scope")}</p>
      )}
    </section>
  );
}
