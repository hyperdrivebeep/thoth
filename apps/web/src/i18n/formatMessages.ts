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
  setupStep: { ko: (step: number) => `처음 설정 · ${step} / 2`, en: (step: number) => `Initial setup · ${step} / 2`, "zh-Hans": (step: number)=>`初始设置 · ${step} / 2` },
  apiKeyLabel: { ko: (provider: string) => `${provider} API 키`, en: (provider: string) => `${provider} API key`, "zh-Hans": (provider: string)=>`${provider} API 密钥` },
  connectionStatus: { ko: (status: string) => `상태: ${status}`, en: (status: string) => `Status: ${status}`, "zh-Hans": (status: string)=>`状态：${status}` },
  keyStatus: { ko: (status: string) => `API 키: ${status} · 계정 로그인과 별도 경로`, en: (status: string) => `API key: ${status} · Separate from account login`, "zh-Hans": (status: string)=>`API 密钥：${status} · 与账户登录是分开的途径` },
  unavailableCode: { ko: (code: string) => `사용 불가 이유: ${code}`, en: (code: string) => `Unavailable: ${code}`, "zh-Hans": (code: string)=>`不可用：${code}` },
  effort: { ko: (value: string) => ` · 추론강도 ${value}`, en: (value: string) => ` · Reasoning effort ${value}`, "zh-Hans": (value: string)=>` · 推理强度 ${value}` },
  savedSelection: { ko: (value: string) => ` 저장된 선택: ${value}.`, en: (value: string) => ` Saved selection: ${value}.`, "zh-Hans": (value: string)=>` 已保存的选择：${value}。` },
  inheritedSelection: { ko: (value: string) => `상속한 프로젝트 선택: ${value}.`, en: (value: string) => `Inherited project selection: ${value}.`, "zh-Hans": (value: string)=>`继承的项目选择：${value}。` },
  excludedModels: { ko: (count: number) => `THOTH가 아직 지원하지 않는 모델 ${count}개`, en: (count: number) => `${count} ${count === 1 ? "model" : "models"} not yet supported by THOTH`, "zh-Hans": (count: number)=>`${count} 个 THOTH 尚不支持的模型` },
  reportedTokens: { ko: (value: string, partial: string) => `보고된 사용 토큰: ${value}${partial}`, en: (value: string, partial: string) => `Reported token usage: ${value}${partial}`, "zh-Hans": (value: string, partial: string)=>`已报告的 token 用量：${value}${partial}` },
  projectMenu: { ko: (name: string) => `${name} 프로젝트 메뉴`, en: (name: string) => `Project menu for ${name}`, "zh-Hans": (name: string)=>`${name} 的项目菜单` },
  archiveProject: {
    ko: (name: string) => `프로젝트 "${name}"을 보관할까요? 보관하면 읽기 전용이 되고 목록에서 숨겨집니다. '보관한 프로젝트 보기'에서 되살릴 수 있습니다.`,
    en: (name: string) => `Archive project "${name}"? It becomes read-only and is hidden from the list. You can restore it from archived projects.`, "zh-Hans": (name: string)=>`要归档项目“${name}”吗？归档后它将变为只读，并从列表中隐藏。你可以在“已归档的项目”中将其恢复。`,
  },
  cutoff: { ko: (value: string | null) => `기준시점 ${value}`, en: (value: string | null) => `Cutoff ${value}`, "zh-Hans": (value: string | null)=>`截止时间 ${value}` },
  deleteProjectTitle: { ko: (name: string) => `프로젝트 "${name}" 영구 삭제`, en: (name: string) => `Delete project "${name}" permanently`, "zh-Hans": (name: string)=>`永久删除项目“${name}”` },
  deleteProjectLabel: { ko: (name: string) => `${name} 영구 삭제`, en: (name: string) => `Delete ${name} permanently`, "zh-Hans": (name: string)=>`永久删除 ${name}` },
  deleteReferenced: {
    ko: (ids: string) => `다른 프로젝트가 이 프로젝트를 참조하고 있어 삭제할 수 없습니다. 참조하는 프로젝트: ${ids}`,
    en: (ids: string) => `Other projects reference this project, so it cannot be deleted. Referencing projects: ${ids}`, "zh-Hans": (ids: string)=>`其他项目引用了此项目，因此无法删除。引用它的项目：${ids}`,
  },
  replyFixed: { ko: (language: string) => `답변: ${language} 고정`, en: (language: string) => `Reply: ${language} (fixed)`, "zh-Hans": (language: string)=>`回答：${language}（已固定）` },
  missingSelection: { ko: (kind: "project" | "thread") => `저장된 ${kind === "project" ? "프로젝트" : "작업"} 선택 확인 필요`, en: (kind: "project" | "thread") => `Check the saved ${kind === "project" ? "project" : "task"} selection`, "zh-Hans": (kind: "project" | "thread") => `请检查已保存的${kind === "project" ? "项目" : "任务"}选择` },
} as const;

const checkedFormats: { [K in keyof typeof formats]: {
  ko: (typeof formats)[K]["ko"];
  en: (...args: Parameters<(typeof formats)[K]["ko"]>) => string;
  "zh-Hans": (...args: Parameters<(typeof formats)[K]["ko"]>) => string;
} } = formats;

/** Every typed sentence with all its languages; the completeness test walks it. */
export const formatCatalog = checkedFormats;

export function message<K extends keyof typeof formats>(key: K, ...args: Parameters<(typeof formats)[K]["ko"]>): string {
  const format = checkedFormats[key][getLocale()] as (...values: Parameters<(typeof formats)[K]["ko"]>) => string;
  return format(...args);
}
