/**
 * The one list of languages the screen can use. Add a language here, then add its message column
 * (i18n/zh/ shows the shape) and its wording in the per-language files; tsc and the catalog test name what is missing.
 * `tag` is the BCP-47 tag for dates and numbers, `browser` says which browser language tags choose it, and
 * `title` is the page title.
 */
export const LANGUAGE_CHOICES = [
  { code: "ko", label: "한국어", tag: "ko-KR", browser: /^ko(-|$)/, title: "THOTH · 연구 작업 공간" },
  { code: "en", label: "English", tag: "en-US", browser: /^en(-|$)/, title: "THOTH · Research workspace" },
  // Traditional Chinese tags (zh-TW, zh-HK, zh-MO, zh-Hant) choose zh-Hant. A script subtag wins over a region:
  // zh-Hans-HK is Simplified. Cantonese tags (yue) are not matched.
  { code: "zh-Hans", label: "中文(简体)", tag: "zh-CN", browser: /^zh(?!-(tw|hk|mo|hant))(-|$)/, title: "THOTH · 研究工作区" },
  // The text follows Taiwan usage; Hong Kong and Macau wording and date formats are not covered.
  { code: "zh-Hant", label: "中文(繁體)", tag: "zh-TW", browser: /^zh-(hant|tw|hk|mo)(-|$)/, title: "THOTH · 研究工作區" },
  { code: "ja", label: "日本語", tag: "ja-JP", browser: /^ja(-|$)/, title: "THOTH · 調査ワークスペース" },
] as const;

export type LanguageCode = (typeof LANGUAGE_CHOICES)[number]["code"];

export function isLanguageCode(value: unknown): value is LanguageCode {
  return LANGUAGE_CHOICES.some(choice => choice.code === value);
}

/** Languages are named in their own writing, the same on every screen language. */
export function languageLabel(code: LanguageCode): string {
  return LANGUAGE_CHOICES.find(choice => choice.code === code)?.label ?? code;
}

const byCode = <K extends keyof (typeof LANGUAGE_CHOICES)[number]>(key: K) =>
  Object.fromEntries(LANGUAGE_CHOICES.map(choice => [choice.code, choice[key]])) as Record<LanguageCode, (typeof LANGUAGE_CHOICES)[number][K]>;

/** The BCP-47 tag of each language, for dates and numbers. */
export const LOCALE_TAGS: Readonly<Record<LanguageCode, string>> = byCode("tag");
/** The page title of each language. */
export const DOCUMENT_TITLES: Readonly<Record<LanguageCode, string>> = byCode("title");

/** The language of the first browser language this app can show; Korean when none matches. */
export function browserLanguage(languages: readonly string[]): LanguageCode {
  for (const tag of languages) {
    const found = LANGUAGE_CHOICES.find(choice => choice.browser.test(tag.toLowerCase()));
    if (found) return found.code;
  }
  return "ko";
}
