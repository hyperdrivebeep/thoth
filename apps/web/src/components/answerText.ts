/** Reads a model answer into paragraphs, lists, bold text and numbered citations. No HTML is ever produced. */

export type Inline =
  | { kind: "text"; text: string; bold: boolean }
  | { kind: "cite"; n: number; spanId: string; bold: boolean };
export type AnswerBlock = { kind: "p"; inline: Inline[] } | { kind: "ul"; items: Inline[][] };
export type Citation = { n: number; spanId: string };
export type ParsedAnswer = { blocks: AnswerBlock[]; citations: Citation[] };

const SPAN = /span:[0-9A-Za-z][0-9A-Za-z_-]*/g;

export function parseAnswer(answer: string): ParsedAnswer {
  const numbers = new Map<string, number>();
  const number = (spanId: string) => {
    const known = numbers.get(spanId);
    if (known) return known;
    numbers.set(spanId, numbers.size + 1);
    return numbers.size;
  };
  const inline = (line: string): Inline[] => {
    const out: Inline[] = [];
    line.split(/(\*\*[^*\n]+?\*\*)/).forEach(part => {
      const bold = part.length > 4 && part.startsWith("**") && part.endsWith("**");
      const body = bold ? part.slice(2, -2) : part;
      let last = 0;
      for (const match of body.matchAll(SPAN)) {
        if (match.index > last) out.push({ kind: "text", text: body.slice(last, match.index), bold });
        out.push({ kind: "cite", n: number(match[0]), spanId: match[0], bold });
        last = match.index + match[0].length;
      }
      if (last < body.length) out.push({ kind: "text", text: body.slice(last), bold });
    });
    return out;
  };
  const blocks: AnswerBlock[] = [];
  for (const paragraph of answer.split(/\n\s*\n/)) {
    const lines = paragraph.split("\n").filter(line => line.trim() !== "");
    if (lines.length === 0) continue;
    if (lines.every(line => /^\s*- /.test(line))) {
      blocks.push({ kind: "ul", items: lines.map(line => inline(line.replace(/^\s*- /, ""))) });
    } else {
      blocks.push({ kind: "p", inline: inline(lines.join("\n")) });
    }
  }
  return { blocks, citations: [...numbers].map(([spanId, n]) => ({ n, spanId })) };
}

/** Source numbers: the answer's own citation numbers first, then any other referenced source in order of appearance. */
export function numberSources(answer: string, otherRefs: string[]): Map<string, number> {
  const numbers = new Map<string, number>(parseAnswer(answer).citations.map(item => [item.spanId, item.n]));
  for (const ref of otherRefs) if (!numbers.has(ref)) numbers.set(ref, numbers.size + 1);
  return numbers;
}
