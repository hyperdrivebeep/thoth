/** Reads a model answer into paragraphs, lists, bold text and numbered citations. No HTML is ever produced. */

export type Inline =
  | { kind: "text"; text: string; bold: boolean }
  | { kind: "cite"; n: number; spanId: string; bold: boolean };
/** A paragraph block with `heading` set is a markdown heading line (`#`–`####`) and is shown as a title, never as "#" text. */
export type AnswerBlock = { kind: "p"; inline: Inline[]; heading?: number } | { kind: "ul"; items: Inline[][] };
export type Citation = { n: number; spanId: string };
export type ParsedAnswer = { blocks: AnswerBlock[]; citations: Citation[] };

const SPAN = /span:[0-9A-Za-z][0-9A-Za-z_-]*/g;
const HEADING = /^\s{0,3}(#{1,4})\s+(.*?)\s*#*\s*$/;
const LIST_ITEM = /^\s*- /;

/**
 * The hold prefix the server puts in front of a partial draft, in each answer language
 * (src/thoth/application/services/request_language.py, _PARTIAL_DRAFT_PREFIX). It is shown as its own line so that a
 * markdown heading the model wrote right after it still starts a line and is read as a heading.
 */
const LEADS = ["검증 보류 · 부분 초안: ", "Review on hold · partial draft: ", "复核暂缓 · 部分草稿：", "検証保留・部分草稿："];

export function splitAnswerLead(answer: string): { lead: string | null; body: string } {
  for (const lead of LEADS) if (answer.startsWith(lead)) return { lead: lead.trim(), body: answer.slice(lead.length) };
  return { lead: null, body: answer };
}

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
    // A heading or a list keeps its own block even when the model wrote it right under a sentence without a blank line.
    let text: string[] = [];
    let items: string[] = [];
    const flush = () => {
      if (text.length) blocks.push({ kind: "p", inline: inline(text.join("\n")) });
      if (items.length) blocks.push({ kind: "ul", items: items.map(item => inline(item)) });
      text = []; items = [];
    };
    for (const line of lines) {
      const heading = HEADING.exec(line);
      if (heading) { flush(); blocks.push({ kind: "p", inline: inline(heading[2]), heading: heading[1].length }); }
      else if (LIST_ITEM.test(line)) { if (text.length) flush(); items.push(line.replace(LIST_ITEM, "")); }
      else { if (items.length) flush(); text.push(line); }
    }
    flush();
  }
  return { blocks, citations: [...numbers].map(([spanId, n]) => ({ n, spanId })) };
}

/** Source numbers: the answer's own citation numbers first, then any other referenced source in order of appearance. */
export function numberSources(answer: string, otherRefs: string[]): Map<string, number> {
  const numbers = new Map<string, number>(parseAnswer(answer).citations.map(item => [item.spanId, item.n]));
  for (const ref of otherRefs) if (!numbers.has(ref)) numbers.set(ref, numbers.size + 1);
  return numbers;
}
