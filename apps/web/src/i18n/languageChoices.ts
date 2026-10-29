/** The one list of languages the screen and the answers can use. Add a language here, then add its message column. */
export const LANGUAGE_CHOICES = [
  { code: "ko", label: "한국어" },
  { code: "en", label: "English" },
] as const;

export type LanguageCode = (typeof LANGUAGE_CHOICES)[number]["code"];

export function isLanguageCode(value: unknown): value is LanguageCode {
  return LANGUAGE_CHOICES.some(choice => choice.code === value);
}

/** Languages are named in their own writing, the same on every screen language. */
export function languageLabel(code: LanguageCode): string {
  return LANGUAGE_CHOICES.find(choice => choice.code === code)?.label ?? code;
}

/** The language of the first browser language this app can show; Korean when none matches. */
export function browserLanguage(languages: readonly string[]): LanguageCode {
  for (const tag of languages) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLanguageCode(base)) return base;
  }
  return "ko";
}
