import type { LanguageCode } from "./languageChoices";

/** A column of translated text for a catalog of { ko, en } messages: tsc rejects a missing or extra key. */
export type ColumnOf<T> = { readonly [K in keyof T]: string };

type Pair = { readonly ko: string; readonly en: string };
/** The languages whose text is a separate column: every screen language but the two the catalogs are written in. */
export type ColumnLanguage = Exclude<LanguageCode, "ko" | "en">;
type Columns<T> = { readonly [L in ColumnLanguage]: ColumnOf<T> };
type Translated<T extends Record<string, Pair>> = { readonly [K in keyof T]: T[K] & { readonly [L in ColumnLanguage]: string } };

/** The catalog with its translated columns added; every language is then read as message[locale]. */
export function withColumns<T extends Record<string, Pair>>(base: T, columns: Columns<T>): Translated<T> {
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(base)) {
    const row: Record<string, string> = { ...base[key] };
    for (const language of Object.keys(columns) as ColumnLanguage[]) row[language] = columns[language][key as keyof T];
    result[key] = row;
  }
  return result as Translated<T>;
}