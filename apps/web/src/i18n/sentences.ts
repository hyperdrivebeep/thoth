import { getLocale } from "./locale";

/** Languages that end a sentence with a full-width mark; nothing is put between their sentences. */
const NO_GAP = new Set(["ja", "zh-Hans", "zh-Hant"]);

/** What goes between two sentences in the current language: a space, or nothing for Japanese and Chinese. */
export function sentenceGap(): string {
  return NO_GAP.has(getLocale()) ? "" : " ";
}

/** Sentences written one after another the way the current language writes them; empty ones are left out. */
export function joinSentences(parts: readonly (string | null | undefined | false)[]): string {
  return parts
    .map(part => (part ? part.trim() : ""))
    .filter(Boolean)
    .join(sentenceGap());
}
