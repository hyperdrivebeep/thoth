/**
 * The one list of languages an answer can be written in (not the screen languages, which are in
 * i18n/languageChoices.ts). Add a language here and in the server's SUPPORTED_RESPONSE_LANGUAGES;
 * the server's workspace/ready list says which of them a given server accepts.
 */
export const RESPONSE_LANGUAGE_CHOICES = [
  { code: "ko", label: "한국어" },
  { code: "en", label: "English" },
  { code: "zh-Hans", label: "中文(简体)" },
] as const;

export type ResponseLanguage = (typeof RESPONSE_LANGUAGE_CHOICES)[number]["code"];

export function isResponseLanguage(value: unknown): value is ResponseLanguage {
  return RESPONSE_LANGUAGE_CHOICES.some(choice => choice.code === value);
}

/** Languages are named in their own writing, the same on every screen language. */
export function responseLanguageLabel(code: ResponseLanguage): string {
  return RESPONSE_LANGUAGE_CHOICES.find(choice => choice.code === code)?.label ?? code;
}

/** The languages of a server's list that this app knows how to offer; a language only the server knows is left out. */
export function knownResponseLanguages(serverList: unknown): ResponseLanguage[] {
  return Array.isArray(serverList) ? serverList.filter(isResponseLanguage) : [];
}