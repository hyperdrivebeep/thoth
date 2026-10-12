import { useSyncExternalStore } from "react";
import { messages, type MessageKey } from "./messages";

export type Locale = "ko" | "en";
const storageKey = "thoth:display-locale:v1";
const listeners = new Set<() => void>();
function stored(): Locale {
  try { return localStorage.getItem(storageKey) === "en" ? "en" : "ko"; } catch { return "ko"; }
}
let locale: Locale = stored();
export const getLocale = () => locale;
export function setLocale(next: Locale): void {
  locale = next;
  try { localStorage.setItem(storageKey, next); } catch { /* The in-memory display choice still works. */ }
  applyDocumentLocale(next);
  listeners.forEach(listener => listener());
}
export function applyDocumentLocale(value: Locale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = value;
  document.title = value === "ko" ? "THOTH · 연구 작업 공간" : "THOTH · Research workspace";
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function useLocale(): Locale { return useSyncExternalStore(subscribe, getLocale, () => "ko"); }
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
