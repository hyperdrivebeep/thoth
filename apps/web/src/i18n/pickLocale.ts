import { getLocale } from "./locale";
import type { LanguageCode } from "./languageChoices";

/** The value written for the current language; a missing language is a type error, never a fallback to English. */
export function pickLocale<T>(values: Readonly<Record<LanguageCode, T>>, selected: LanguageCode = getLocale()): T {
  return values[selected];
}
