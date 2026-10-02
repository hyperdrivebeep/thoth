import { Fragment, useMemo } from "react";
import { parseAnswer, type Inline } from "./answerText";

/** The answer as paragraphs, lists and bold text, with each source shown as a numbered button. */
export function AnswerBody({ answer, onCite }: {
  answer: string;
  onCite: (spanId: string, numbers: Record<string, number>) => void;
}) {
  const parsed = useMemo(() => parseAnswer(answer), [answer]);
  const numbers = useMemo(() => Object.fromEntries(parsed.citations.map(item => [item.spanId, item.n])), [parsed]);
  const render = (items: Inline[]) => items.map((item, index) => {
    const body = item.kind === "text"
      ? item.text
      : <button type="button" className="answer-citation" title={item.spanId} aria-label={`근거 ${item.n} 보기`} onClick={() => onCite(item.spanId, numbers)}>[{item.n}]</button>;
    return <Fragment key={index}>{item.bold ? <strong>{body}</strong> : body}</Fragment>;
  });
  return <div className="answer-text">{parsed.blocks.map((block, index) => block.kind === "p"
    ? <p key={index}>{render(block.inline)}</p>
    : <ul key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}>{render(item)}</li>)}</ul>)}</div>;
}
