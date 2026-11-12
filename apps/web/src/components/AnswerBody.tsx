import { message } from "../i18n/formatMessages";
import type { ResponseLanguage } from "../api/responseLanguages";
import { Fragment, useMemo } from "react";
import { parseAnswer, splitAnswerLead, type Inline } from "./answerText";

/** The answer as a hold lead line, headings, paragraphs, lists and bold text, with each source shown as a numbered button. */
export function AnswerBody({ answer, onCite, language }: {
  answer: string;
  language?: ResponseLanguage | null;
  onCite: (spanId: string, numbers: Record<string, number>) => void;
}) {
  const { lead, body } = useMemo(() => splitAnswerLead(answer), [answer]);
  const parsed = useMemo(() => parseAnswer(body), [body]);
  const numbers = useMemo(() => Object.fromEntries(parsed.citations.map(item => [item.spanId, item.n])), [parsed]);
  const render = (items: Inline[]) => items.map((item, index) => {
    const body = item.kind === "text"
      ? item.text
      : <button type="button" className="answer-citation" title={message("evidenceButton", item.n)} aria-label={message("evidenceButton", item.n)} onClick={() => onCite(item.spanId, numbers)}>[{item.n}]</button>;
    return <Fragment key={index}>{item.bold ? <strong>{body}</strong> : body}</Fragment>;
  });
  return <div className="answer-text" lang={language ?? undefined}>
    {lead && <p className="answer-lead"><strong>{lead}</strong></p>}
    {parsed.blocks.map((block, index) => block.kind === "ul"
      ? <ul key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}>{render(item)}</li>)}</ul>
      : block.heading ? <h4 key={index} className="answer-heading">{render(block.inline)}</h4>
      : <p key={index}>{render(block.inline)}</p>)}
  </div>;
}
