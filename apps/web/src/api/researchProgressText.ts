/** Locale dispatch for display-only progress wording; canonical state and event identity stay upstream. */
import { getLocale } from "../i18n/locale";
import * as ko from "../i18n/progressKo";
import * as en from "../i18n/progressEn";
import * as zh from "../i18n/progressZh";
export type {ProgressScene, TimelineIcon, WorkFacts} from "../i18n/progressKo";
export {clip, unique, compactPayload, locatorPayload} from "../i18n/progressKo";
type Wording = Pick<typeof ko, "ACTION_COPY" | "formatDuration" | "formatBytes" | "sceneFor" | "waitLine" | "usageLine" | "missingFor" | "stageActionPayload" | "modelActionPayload" | "hypothesisActionPayload">;
const wording: Record<ReturnType<typeof getLocale>, Wording> = { ko, en, "zh-Hans": zh };
const current = () => wording[getLocale()];
export const ACTION_COPY: typeof ko.ACTION_COPY = Object.fromEntries(Object.keys(ko.ACTION_COPY).map(key => [key, {
  get label() { return current().ACTION_COPY[key].label; }, icon: ko.ACTION_COPY[key].icon,
}]));
export const formatDuration = (...args: Parameters<typeof ko.formatDuration>): ReturnType<typeof ko.formatDuration> => current().formatDuration(...args);
export const formatBytes = (...args: Parameters<typeof ko.formatBytes>): ReturnType<typeof ko.formatBytes> => current().formatBytes(...args);
export const sceneFor = (...args: Parameters<typeof ko.sceneFor>): ReturnType<typeof ko.sceneFor> => current().sceneFor(...args);
export const waitLine = (...args: Parameters<typeof ko.waitLine>): ReturnType<typeof ko.waitLine> => current().waitLine(...args);
export const usageLine = (...args: Parameters<typeof ko.usageLine>): ReturnType<typeof ko.usageLine> => current().usageLine(...args);
export const missingFor = (...args: Parameters<typeof ko.missingFor>): ReturnType<typeof ko.missingFor> => current().missingFor(...args);
export const stageActionPayload = (...args: Parameters<typeof ko.stageActionPayload>): ReturnType<typeof ko.stageActionPayload> => current().stageActionPayload(...args);
export const modelActionPayload = (...args: Parameters<typeof ko.modelActionPayload>): ReturnType<typeof ko.modelActionPayload> => current().modelActionPayload(...args);
export const hypothesisActionPayload = (...args: Parameters<typeof ko.hypothesisActionPayload>): ReturnType<typeof ko.hypothesisActionPayload> => current().hypothesisActionPayload(...args);