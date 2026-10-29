import { useSyncExternalStore } from "react";
import { messages, type MessageKey } from "./messages";
import { browserLanguage, isLanguageCode, type LanguageCode } from "./languageChoices";

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
  } catch { return "auto"; }
}
function browserLocale(): Locale {
  if (typeof navigator === "undefined") return "ko";
  return browserLanguage(navigator.languages?.length ? navigator.languages : [navigator.language ?? ""]);
}
const resolve = (value: DisplayPreference): Locale => value === "auto" ? browserLocale() : value;
let preference: DisplayPreference = storedPreference();
let locale: Locale = resolve(preference);
export const getLocale = () => locale;
export const getDisplayPreference = () => preference;
export function setDisplayPreference(next: DisplayPreference): void {
  preference = next;
  locale = resolve(next);
  try { localStorage.setItem(storageKey, next); } catch { /* The in-memory display choice still works. */ }
  applyDocumentLocale(locale);
  listeners.forEach(listener => listener());
}
export const setLocale = (next: Locale): void => setDisplayPreference(next);
// With "auto", a change of the browser language is followed while the page is open.
if (typeof window !== "undefined") {
  window.addEventListener("languagechange", () => { if (preference === "auto") setDisplayPreference("auto"); });
}
export function applyDocumentLocale(value: Locale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = value;
  document.title = value === "ko" ? "THOTH · 연구 작업 공간" : "THOTH · Research workspace";
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function useLocale(): Locale { return useSyncExternalStore(subscribe, getLocale, () => "ko"); }
export function useDisplayPreference(): DisplayPreference { return useSyncExternalStore(subscribe, getDisplayPreference, () => "auto" as const); }
export function t(key: MessageKey, selectedLocale: Locale = locale): string { return messages[key][selectedLocale]; }
export function useTranslation() { useLocale(); return t; }
export function messageKey(key: MessageKey): MessageKey { return key; }
const legacyUiText = new Map<string, MessageKey>(Object.entries(messages).flatMap(([key, pair]) =>
  [[pair.ko, key as MessageKey], [pair.en, key as MessageKey]]));
/** Only call for application-owned notices/errors, never model answers or user/source text. */
export function uiText(value: string): string {
  const key = Object.hasOwn(messages, value) ? value as MessageKey : legacyUiText.get(value);
  return key ? t(key) : value;
}
export function formatDate(value: string | number | Date): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString(locale === "ko" ? "ko-KR" : "en-US");
}
export function formatNumber(value: number): string { return new Intl.NumberFormat(locale === "ko" ? "ko-KR" : "en-US").format(value); }
export function hypothesisLabel(number: string | number): string { return locale === "ko" ? `가설 ${number}` : `Hypothesis ${number}`; }
export function evidenceCount(count: number): string { return locale === "ko" ? `근거 ${formatNumber(count)}개` : `${formatNumber(count)} evidence ${count === 1 ? "reference" : "references"}`; }
