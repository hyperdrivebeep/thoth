import { Icon, Spinner } from "@blueprintjs/core";

import { liveResearchProgress } from "../api/researchProgress";
import type { ResearchStatus } from "../api/research";
import { Disclosure } from "./Disclosure";
import { ResearchActivityStrip } from "./ResearchActivityStrip";

export function ResearchLiveProgress({
  status,
  sourceUris = [],
}: {
  status?: ResearchStatus;
  sourceUris?: string[];
}) {
  const progress = liveResearchProgress(status, sourceUris);
  if (!progress) {
    return null;
  }
  const notes = progress.events.filter(event => event.kind === "note" && !event.live).map(event=>event.text);
  const contextNotes = [...new Set([notes[0],notes.at(-1)])].filter((note):note is string=>Boolean(note)&&note!==progress.current.title);
  const evidenceScope = progress.evidenceLabels.length>0
    ? `${progress.evidenceLabels.slice(0,2).join(" · ")}${progress.evidenceLabels.length>2?` 외 ${progress.evidenceLabels.length-2}곳`:""}`
    : progress.evidenceTotal===0 ? "현재 연결 범위에서 선택된 근거 후보가 없습니다." : null;
  const judgement = progress.hypothesisCount==null ? progress.missing
    : `가설 ${progress.hypothesisCount}개 중 ${progress.reviewCount??0}개를 검토했습니다.`;
  return (
    <aside className="research-progress" aria-label="연구 진행 상황">
      <div className="research-progress-summary" role="status" aria-live="polite" aria-atomic="true">
        {status?.operation_state === "RUNNING" ? <Spinner size={18}/> : <Icon icon="tick-circle" size={18}/>}<div><span>지금</span><strong>{progress.current.title}</strong></div>
      </div>
      <div className="research-progress-brief">
        <div><span>왜</span><p>{progress.current.detail}</p></div>
        {contextNotes.length>0&&<div><span>확인한 과정</span><p>{contextNotes.join(" · ")}</p></div>}
        {evidenceScope&&<div><span>근거 범위</span><p>{evidenceScope}</p></div>}
        {judgement&&<div><span>판단 상태</span><p>{judgement}</p></div>}
      </div>
      <ResearchActivityStrip events={status?.user_activity_events} />
      <Disclosure label="작업 상세">
        <ol className="research-progress-timeline">
          {progress.events.map((event) =>
            event.kind === "note" ? (
              <li key={event.id} className={event.live ? "research-progress-note live" : "research-progress-note"}>
                {event.text}
              </li>
            ) : (
              <li key={event.id} className={event.live ? "research-progress-action live" : "research-progress-action"}>
                {event.live ? <Spinner size={16} /> : <Icon icon={event.icon ?? "tick-circle"} size={14} />}
                <span className="research-progress-action-label">{event.label}</span>
                {event.payload ? <code>{event.payload}</code> : null}
              </li>
            ),
          )}
        </ol>
        {(progress.modelLine || progress.usageLine) && (
          <p className="research-progress-model">
            {[progress.modelLine, progress.usageLine].filter(Boolean).join(" · ")}
          </p>
        )}
      </Disclosure>
    </aside>
  );
}
