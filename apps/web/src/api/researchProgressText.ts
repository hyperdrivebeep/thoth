/** Locale dispatch for display-only progress wording; canonical state and event identity stay upstream. */
import { getLocale } from "../i18n/locale";
import * as ko from "../i18n/progressKo";
import * as en from "../i18n/progressEn";
export type {ProgressScene, TimelineIcon, WorkFacts} from "../i18n/progressKo";
export {clip, unique, compactPayload, locatorPayload} from "../i18n/progressKo";
export const ACTION_COPY: typeof ko.ACTION_COPY = Object.fromEntries(Object.keys(ko.ACTION_COPY).map(key => [key, {
  get label() { return (getLocale() === "en" ? en : ko).ACTION_COPY[key].label; }, icon: ko.ACTION_COPY[key].icon,
}]));
export const formatDuration = (...args: Parameters<typeof ko.formatDuration>): ReturnType<typeof ko.formatDuration> => (getLocale() === "en" ? en : ko).formatDuration(...args);
export const formatBytes = (...args: Parameters<typeof ko.formatBytes>): ReturnType<typeof ko.formatBytes> => (getLocale() === "en" ? en : ko).formatBytes(...args);
export const sceneFor = (...args: Parameters<typeof ko.sceneFor>): ReturnType<typeof ko.sceneFor> => (getLocale() === "en" ? en : ko).sceneFor(...args);
export const waitLine = (...args: Parameters<typeof ko.waitLine>): ReturnType<typeof ko.waitLine> => (getLocale() === "en" ? en : ko).waitLine(...args);
export const usageLine = (...args: Parameters<typeof ko.usageLine>): ReturnType<typeof ko.usageLine> => (getLocale() === "en" ? en : ko).usageLine(...args);
export const missingFor = (...args: Parameters<typeof ko.missingFor>): ReturnType<typeof ko.missingFor> => (getLocale() === "en" ? en : ko).missingFor(...args);
export const stageActionPayload = (...args: Parameters<typeof ko.stageActionPayload>): ReturnType<typeof ko.stageActionPayload> => (getLocale() === "en" ? en : ko).stageActionPayload(...args);
export const modelActionPayload = (...args: Parameters<typeof ko.modelActionPayload>): ReturnType<typeof ko.modelActionPayload> => (getLocale() === "en" ? en : ko).modelActionPayload(...args);
export const hypothesisActionPayload = (...args: Parameters<typeof ko.hypothesisActionPayload>): ReturnType<typeof ko.hypothesisActionPayload> => (getLocale() === "en" ? en : ko).hypothesisActionPayload(...args);
