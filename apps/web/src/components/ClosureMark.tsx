import { Button, Tag } from "@blueprintjs/core";
import type { ClosureRow } from "../api/judgmentRecords";
import type { Verdict } from "../api/trace";
import { Disclosure } from "./Disclosure";
import { closureDisplay, eventKindLabel, eventLines, isMet, previousEvents } from "./judgmentRecordText";

/**
 * Beside a row's verdict, never in it: what a person recorded about closing the row, and the button to record one.
 * The verdict's own tag, words and colour are not touched. Only "effect confirmed" is the rules' word.
 * Older records fold under the latest one, newest first; the effect-confirmed mark belongs to the latest record only.
 */
export function ClosureMark({ row, verdict, label, onRecord }: { row: ClosureRow | undefined; verdict: Verdict; label: string; onRecord: (verdict: Verdict) => void }) {
  const shown = row && row.events.length > 0 ? closureDisplay(row) : null;
  const older = row ? previousEvents(row) : [];
  return <div className="trace-closure-cell">
    {shown && <div className="trace-closure" role="note"><Tag minimal intent="primary" icon="annotation">{shown.label}</Tag>
      <ul className="trace-reasons">{shown.lines.map((line, index) => <li key={index}>{line}</li>)}</ul></div>}
    {row && older.length > 0 && <Disclosure label={`이전 처분 기록 ${older.length}건`} className="trace-closure-history">
      {older.map(event => <div className="trace-closure" role="note" key={event.event_id}><Tag minimal icon="history">{eventKindLabel(event)}</Tag>
        <ul className="trace-reasons">{eventLines(row.subject_kind, event).map((line, index) => <li key={index}>{line}</li>)}</ul></div>)}
    </Disclosure>}
    {!isMet(verdict.state) && <Button small minimal icon="annotation" aria-label={label + " 처분 기록 남기기"} onClick={() => onRecord(verdict)}>처분 기록</Button>}
  </div>;
}
