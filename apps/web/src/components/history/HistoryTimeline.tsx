import { Button, Icon, Tag } from "@blueprintjs/core";
import type { HistoryPage, HistoryRow, HistorySelection } from "../../api/historyModels";
import { selectionKey } from "../../api/historyModels";
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
  return <section className="history-timeline" aria-label="연구 이력 목록">
    <div className="history-list-heading"><h2>기록된 흐름</h2><span>최신 기록부터</span></div>
    {partial && <p className="history-coverage" role="status">현재 읽을 수 있는 기록입니다. 일부 이력의 연결이나 조회 범위는 확인이 필요합니다.</p>}
    {items.length === 0 && <div className="history-empty">
      <Icon icon="history" size={28} />
      <h3>{onlyCheckpoints ? "이 구간에는 중간 저장 기록이 있습니다" : last?.nextCursor || partial ? "이 구간에서 표시할 기록이 없습니다" : "아직 연구 기록이 없습니다"}</h3>
      <p>{onlyCheckpoints ? "‘중간 저장 포함’을 선택하면 확인할 수 있습니다." : last?.nextCursor ? "다음 구간의 기록을 계속 확인할 수 있습니다." : partial ? "전체 이력이 없다는 뜻은 아닙니다. 조회 범위와 접근 상태를 확인하세요." : "질문과 답변, 판단의 변경이 여기에 남습니다."}</p>
    </div>}
    <ol className="history-events">
      {items.map(item => <li key={item.id}>
        <Button minimal alignText="left" className={`history-event ${selected && selectionKey(selected) === selectionKey(item.selection) ? "is-selected" : ""}`}
          aria-pressed={Boolean(selected && selectionKey(selected) === selectionKey(item.selection))} onClick={() => onSelect(item)}>
          <span className="history-event-meta"><span>{item.completion === "CHECKPOINT" ? "중간 저장" : kindLabel(item.kind)}</span><time dateTime={item.occurredAt}>{formatHistoryTime(item.occurredAt)}</time></span>
          <strong>{item.title}</strong>
          <span className="history-event-status"><Tag minimal intent={currentnessTone(item.currentness)}>{currentnessLabel(item.currentness)}</Tag>
            {item.membership === "BRANCH" && <Tag minimal>다른 분기</Tag>}
            {item.membership === "ANCESTOR" && <span>이전 버전</span>}
            {item.association === "RELATED_OBJECT" && <span>관련 항목 · 사용 여부 미확인</span>}
          </span>
        </Button>
        {comparison && item.kind === "RESULT" && <div className="history-compare-choices">
          {item.selection.kind === "result" && item.selection.resultDigest && item.availability !== "UNAVAILABLE" ? <>
            <Button small minimal aria-pressed={comparison?.before ? selectionKey(comparison.before) === selectionKey(item.selection) : false}
              onClick={() => comparison?.onChoose("before", item)}>비교 전</Button>
            <Button small minimal aria-pressed={comparison?.after ? selectionKey(comparison.after) === selectionKey(item.selection) : false}
              onClick={() => comparison?.onChoose("after", item)}>비교 후</Button>
          </> : <span className="history-compare-unavailable">정확한 요청·결과 연결이 없어 비교할 수 없습니다.</span>}
        </div>}
      </li>)}
    </ol>
    {last?.nextCursor && <Button fill minimal icon="chevron-down" loading={loadingMore} onClick={onMore}>다음 기록 더 보기</Button>}
  </section>;
}
