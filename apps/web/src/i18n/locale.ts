import { useSyncExternalStore } from "react";
import { messages, type MessageKey } from "./messages";
import {
  browserLanguage,
  DOCUMENT_TITLES,
  isLanguageCode,
  LOCALE_TAGS,
  type LanguageCode,
} from "./languageChoices";

export type Locale = LanguageCode;
/** What the person chose: a language, or "auto" to follow the browser. */
export type DisplayPreference = "auto" | Locale;
const storageKey = "thoth:display-locale:v1";
const listeners = new Set<() => void>();
/** A choice saved before "auto" existed is a language name and keeps meaning that language. */
function storedPreference(): DisplayPreference {
  try {
    const value = localStorage.getItem(storageKey);
    return isLanguageCode(value) ? value : "auto";
  } catch {
    return "auto";
  }
}
function browserLocale(): Locale {
  if (typeof navigator === "undefined") return "ko";
  return browserLanguage(
    navigator.languages?.length ? navigator.languages : [navigator.language ?? ""],
  );
}
const resolve = (value: DisplayPreference): Locale => (value === "auto" ? browserLocale() : value);
let preference: DisplayPreference = storedPreference();
let locale: Locale = resolve(preference);
export const getLocale = () => locale;
export const getDisplayPreference = () => preference;
export function setDisplayPreference(next: DisplayPreference): void {
  preference = next;
  locale = resolve(next);
  try {
    localStorage.setItem(storageKey, next);
  } catch {
    /* The in-memory display choice still works. */
  }
  applyDocumentLocale(locale);
  listeners.forEach((listener) => listener());
}
export const setLocale = (next: Locale): void => setDisplayPreference(next);
// With "auto", a change of the browser language is followed while the page is open.
if (typeof window !== "undefined") {
  window.addEventListener("languagechange", () => {
    if (preference === "auto") setDisplayPreference("auto");
  });
}
export function applyDocumentLocale(value: Locale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = value;
  document.title = DOCUMENT_TITLES[value];
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale, () => "ko");
}
export function useDisplayPreference(): DisplayPreference {
  return useSyncExternalStore(subscribe, getDisplayPreference, () => "auto" as const);
}
export function t(key: MessageKey, selectedLocale: Locale = locale): string {
  return messages[key][selectedLocale];
}
export function useTranslation() {
  useLocale();
  return t;
}
export function messageKey(key: MessageKey): MessageKey {
  return key;
}
const legacyUiText = new Map<string, MessageKey>(
  Object.entries(messages).flatMap(([key, pair]) => [
    [pair.ko, key as MessageKey],
    [pair.en, key as MessageKey],
    [pair["zh-Hans"], key as MessageKey],
    [pair["zh-Hant"], key as MessageKey],
  ]),
);
// Japanese joined later. A notice saved in Japanese is found too; a text the four lists above
// already hold keeps the key it had.
for (const [key, pair] of Object.entries(messages)) {
  if (!legacyUiText.has(pair.ja)) legacyUiText.set(pair.ja, key as MessageKey);
}
/** Only call for application-owned notices/errors, never model answers or user/source text. */
export function uiText(value: string): string {
  const key = Object.hasOwn(messages, value) ? (value as MessageKey) : legacyUiText.get(value);
  return key ? t(key) : value;
}
export function formatDate(value: string | number | Date): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString(LOCALE_TAGS[locale]);
}
/** The BCP-47 tag of the current language, for toLocaleString and Intl. */
export const localeTag = (selectedLocale: Locale = locale): string => LOCALE_TAGS[selectedLocale];
export function formatNumber(value: number): string {
  return new Intl.NumberFormat(LOCALE_TAGS[locale]).format(value);
}
export function hypothesisLabel(number: string | number): string {
  return {
    ko: `가설 ${number}`,
    en: `Hypothesis ${number}`,
    "zh-Hans": `假设 ${number}`,
    "zh-Hant": `假設 ${number}`,
    ja: `仮説 ${number}`,
  }[locale];
}
export function evidenceCount(count: number): string {
  return {
    ko: `근거 ${formatNumber(count)}개`,
    en: `${formatNumber(count)} evidence ${count === 1 ? "reference" : "references"}`,
    "zh-Hans": `${formatNumber(count)} 条证据`,
    "zh-Hant": `${formatNumber(count)} 條證據`,
    ja: `根拠 ${formatNumber(count)} 件`,
  }[locale];
}
