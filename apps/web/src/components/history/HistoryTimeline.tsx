import { t } from "../../i18n/locale";
import { historyTitle } from "../../i18n/historyTitle";
import { Button, Icon, Tag } from "@blueprintjs/core";
import type { HistoryPage, HistoryRow, HistorySelection } from "../../api/historyModels";
import { selectionKey } from "../../api/historyModels";
import { ChangeBrief } from "./HistoryChangeSummary";
import { currentnessLabel, currentnessTone, formatHistoryTime, kindLabel } from "./historyPresentation";

export function HistoryTimeline({ pages, selected, loadingMore, onSelect, onMore, showCheckpoints = false, comparison }: {
  pages: HistoryPage[];
  selected: HistorySelection | null;
  loadingMore: boolean;
  onSelect: (item: HistoryRow) => void;
  onMore: () => void;
  showCheckpoints?: boolean;
  comparison?: { before: HistorySelection | null; after: HistorySelection | null; onChoose: (side: "before" | "after", row: HistoryRow) => void };
}) {
  const unique = new Map<string, HistoryRow>();
  for (const page of pages) for (const row of page.items) unique.set(row.id, row);
  const allItems = [...unique.values()];
  const items = showCheckpoints ? allItems : allItems.filter(item => item.completion !== "CHECKPOINT");
  const onlyCheckpoints = allItems.length > 0 && items.length === 0;
  const last = pages.at(-1);
  const partial = pages.some(page => page.coverage.association !== "EXACT" || page.coverage.scan !== "COMPLETE_PAGE");
  return <section className="history-timeline" aria-label={t("timeline.list")}>
    <div className="history-list-heading"><h2>{t("timeline.flow")}</h2><span>{t("timeline.newest")}</span></div>
    {partial && <p className="history-coverage" role="status">{t("timeline.coverage")}</p>}
    {items.length === 0 && <div className="history-empty">
      <Icon icon="history" size={28} />
      <h3>{onlyCheckpoints ? t("timeline.hasCheckpoints") : last?.nextCursor || partial ? t("timeline.emptyPage") : t("timeline.emptyHistory")}</h3>
      <p>{onlyCheckpoints ? t("timeline.includeCheckpoints") : last?.nextCursor ? t("timeline.morePages") : partial ? t("timeline.notAllEmpty") : t("timeline.futureRecords")}</p>
    </div>}
    <ol className="history-events">
      {items.map(item => <li key={item.id}>
        <Button minimal alignText="left" className={`history-event ${selected && selectionKey(selected) === selectionKey(item.selection) ? "is-selected" : ""}`}
          aria-pressed={Boolean(selected && selectionKey(selected) === selectionKey(item.selection))} onClick={() => onSelect(item)}>
          <span className="history-event-meta"><span>{item.completion === "CHECKPOINT" ? t("timeline.checkpoint") : kindLabel(item.kind)}</span><time dateTime={item.occurredAt}>{formatHistoryTime(item.occurredAt)}</time></span>
          <strong>{historyTitle(item)}</strong>
          <ChangeBrief summary={item.changeSummary}/>
          <span className="history-event-status"><Tag minimal intent={currentnessTone(item.currentness)}>{currentnessLabel(item.currentness)}</Tag>
            {item.membership === "BRANCH" && <Tag minimal>{t("timeline.otherBranch")}</Tag>}
            {item.membership === "ANCESTOR" && <span>{t("timeline.previousVersion")}</span>}
            {item.association === "RELATED_OBJECT" && <span>{t("timeline.relatedUnknown")}</span>}
          </span>
        </Button>
        {comparison && item.kind === "RESULT" && <div className="history-compare-choices">
          {item.selection.kind === "result" && item.selection.resultDigest && item.availability !== "UNAVAILABLE" ? <>
            <Button small minimal aria-pressed={comparison?.before ? selectionKey(comparison.before) === selectionKey(item.selection) : false}
              onClick={() => comparison?.onChoose("before", item)}>{t("delta.before")}</Button>
            <Button small minimal aria-pressed={comparison?.after ? selectionKey(comparison.after) === selectionKey(item.selection) : false}
              onClick={() => comparison?.onChoose("after", item)}>{t("delta.after")}</Button>
          </> : <span className="history-compare-unavailable">{t("timeline.noExactLink")}</span>}
        </div>}
      </li>)}
    </ol>
    {last?.nextCursor && <Button fill minimal icon="chevron-down" loading={loadingMore} onClick={onMore}>{t("timeline.nextRecords")}</Button>}
  </section>;
}
