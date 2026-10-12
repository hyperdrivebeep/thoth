import { countFormats } from "./countFormatMessages";
import { stateFormats } from "./stateFormatMessages";
import { metricFormats } from "./metricFormatMessages";
import { historyFormats } from "./historyFormatMessages";
import { uiFormats } from "./uiFormatMessages";
import { traceFormats } from "./traceFormatMessages";
import { getLocale } from "./locale";

/** Arguments are typed per complete message, rather than assembled with Korean particles. */
const formats = {
  ...countFormats,
  ...stateFormats,
  ...metricFormats,
  ...historyFormats,
  ...uiFormats,
  ...traceFormats,
  setupStep: { ko: (step: number) => `처음 설정 · ${step} / 2`, en: (step: number) => `Initial setup · ${step} / 2` },
  apiKeyLabel: { ko: (provider: string) => `${provider} API 키`, en: (provider: string) => `${provider} API key` },
  connectionStatus: { ko: (status: string) => `상태: ${status}`, en: (status: string) => `Status: ${status}` },
  keyStatus: { ko: (status: string) => `API 키: ${status} · 계정 로그인과 별도 경로`, en: (status: string) => `API key: ${status} · Separate from account login` },
  unavailableCode: { ko: (code: string) => `사용 불가 이유: ${code}`, en: (code: string) => `Unavailable: ${code}` },
  effort: { ko: (value: string) => ` · 추론강도 ${value}`, en: (value: string) => ` · Reasoning effort ${value}` },
  savedSelection: { ko: (value: string) => ` 저장된 선택: ${value}.`, en: (value: string) => ` Saved selection: ${value}.` },
  inheritedSelection: { ko: (value: string) => `상속한 프로젝트 선택: ${value}.`, en: (value: string) => `Inherited project selection: ${value}.` },
  excludedModels: { ko: (count: number) => `THOTH가 아직 지원하지 않는 모델 ${count}개`, en: (count: number) => `${count} ${count === 1 ? "model" : "models"} not yet supported by THOTH` },
  reportedTokens: { ko: (value: string, partial: string) => `보고된 사용 토큰: ${value}${partial}`, en: (value: string, partial: string) => `Reported token usage: ${value}${partial}` },
  projectMenu: { ko: (name: string) => `${name} 프로젝트 메뉴`, en: (name: string) => `Project menu for ${name}` },
  archiveProject: {
    ko: (name: string) => `프로젝트 "${name}"을 보관할까요? 보관하면 읽기 전용이 되고 목록에서 숨겨집니다. '보관한 프로젝트 보기'에서 되살릴 수 있습니다.`,
    en: (name: string) => `Archive project "${name}"? It becomes read-only and is hidden from the list. You can restore it from archived projects.`,
  },
  cutoff: { ko: (value: string | null) => `기준시점 ${value}`, en: (value: string | null) => `Cutoff ${value}` },
  missingSelection: { ko: (kind: "project" | "thread") => `저장된 ${kind === "project" ? "프로젝트" : "작업"} 선택 확인 필요`, en: (kind: "project" | "thread") => `Check the saved ${kind === "project" ? "project" : "task"} selection` },
} as const;

const checkedFormats: { [K in keyof typeof formats]: {
  ko: (typeof formats)[K]["ko"];
  en: (...args: Parameters<(typeof formats)[K]["ko"]>) => string;
} } = formats;

export function message<K extends keyof typeof formats>(key: K, ...args: Parameters<(typeof formats)[K]["ko"]>): string {
  const format = checkedFormats[key][getLocale()] as (...values: Parameters<(typeof formats)[K]["ko"]>) => string;
  return format(...args);
}
