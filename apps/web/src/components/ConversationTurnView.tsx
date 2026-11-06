import { message } from "../i18n/formatMessages";
import { localeTag, t } from "../i18n/locale";
import { Button, Tag } from "@blueprintjs/core";
import { useState, type ReactNode } from "react";
import type { ConversationTurn } from "../api/conversation";
import { turnSummary } from "./turnSummary";

/** The question bubble of one turn: heading by edit kind, time, text. */
export function QuestionBubble({ heading, time, text }: { heading: string; time?: string | null; text: string }) {
  return <article className="user-message"><header>{heading}{time ? <time dateTime={time}>{new Date(time).toLocaleString(localeTag())}</time> : null}</header><p>{text}</p></article>;
}

/**
 * One past turn of the conversation. Folded by default so the newest turn stays in view: the question, then one line
 * with the turn number, its state tags (read from the same facts as the result card) and a preview of the answer.
 * "Expand" opens the full result card in place; a cut-off turn keeps its "ask again" button on the folded line.
 */
export function ConversationTurnView({ turn, index, expanded: expandedAtFirst, onRetry, children }: {
  turn: ConversationTurn; index: number;
  /** The newest turn starts open; earlier turns start folded. */
  expanded: boolean;
  onRetry?: () => void;
  /** The full result card for this turn. */
  children: ReactNode;
}) {
  const [open, setOpen] = useState(expandedAtFirst);
  const summary = turnSummary(turn);
  const label = message("turnIndex", index);
  return <div className="conversation-turn" data-turn-index={index} data-turn-open={open}>
    <QuestionBubble heading={message("questionHeading", turn.input.edit_kind)} time={turn.input.created_at} text={turn.input.text}/>
    {open ? <>
      {!expandedAtFirst && <div className="turn-summary turn-summary-open"><span className="turn-summary-label">{label}</span>
        <Button small minimal icon="chevron-up" aria-expanded onClick={() => setOpen(false)}>{t("timeline.collapseTurn")}</Button></div>}
      {children}
    </> : <div className="turn-summary" role="group" aria-label={label}>
      <div className="turn-summary-line">
        <span className="turn-summary-label">{label}</span>
        {summary.tags.map(tag => <Tag key={tag.text} minimal intent={tag.intent}>{tag.text}</Tag>)}
        {summary.note && <span className="muted">{summary.note}</span>}
        {summary.preview && <span className="turn-summary-preview">{summary.preview}</span>}
      </div>
      <div className="turn-summary-actions">
        {onRetry && !summary.hasAnswer && <Button small icon="refresh" onClick={onRetry}>{t("timeline.retryShort")}</Button>}
        <Button small minimal icon="chevron-down" aria-expanded={false} onClick={() => setOpen(true)}>{t("timeline.expandTurn")}</Button>
      </div>
    </div>}
  </div>;
}
