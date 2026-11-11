/** A paragraph longer than this is hard to read as one block (04-ideal I-29). */
const LONG_PARAGRAPH = 120;

/** Where one sentence ends and the next begins: after its end mark, before the following text. */
const SENTENCE_BREAK = /(?<=[.!?])\s+(?=[^\s(])|(?<=[。！？])(?=[^\s）」”])/;

/**
 * An explanation shown as text. A short one stays one line of prose; a long one is shown one sentence
 * per line, so that each point can be found again.
 */
export function Sentences({ text }: { text: string }) {
  const parts = text.length > LONG_PARAGRAPH ? text.split(SENTENCE_BREAK).filter(Boolean) : [text];
  if (parts.length < 2) return <>{text}</>;
  return (
    <>
      {parts.map((part, index) => (
        <span className="sentence-line" key={index}>
          {part}
        </span>
      ))}
    </>
  );
}
