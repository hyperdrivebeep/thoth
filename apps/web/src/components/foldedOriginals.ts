import { plainReason, splitReadable } from "./plainWording";

/** The originals behind a readable list: texts shown in other words, and untranslated English. */
export function foldedOriginals(items: string[], responseLanguage?: string | null): string[] {
  const { technical } = splitReadable(items, responseLanguage);
  return [...items.filter(item => plainReason(item) !== null), ...technical];
}
