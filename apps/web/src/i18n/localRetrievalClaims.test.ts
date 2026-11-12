import { expect, it } from "vitest";
import { LANGUAGE_CHOICES } from "./languageChoices";
import { localRetrievalText } from "./localRetrievalMessages";

/* Local retrieval is on for every project once the model files and the runtime component are installed. The texts
   next to the switch must say that, say that an index that is not ready (or cannot be used for a question) means the
   existing search, and must not keep calling it an experiment, off by default, or something started only by a click. */
const KEYS = [
  "localRetrieval.intro",
  "localRetrieval.readyNote",
  "localRetrieval.enableHelp",
  "localRetrieval.outdatedIndex",
] as const;
const OLD_CLAIMS: Record<string, RegExp[]> = {
  ko: [/실험/, /기본은 꺼짐/, /눌러야만/],
  en: [/experimental/i, /off by default/i, /only when you choose/i],
  "zh-Hans": [/实验/, /默认关闭/, /才会开始/],
  "zh-Hant": [/實驗/, /預設關閉/, /才會開始/],
  ja: [/実験/, /既定ではオフ/, /だけ始まります/],
};
const AUTOMATIC: Record<string, RegExp> = {
  ko: /자동으로/,
  en: /automatically/i,
  "zh-Hans": /自动/,
  "zh-Hant": /自動/,
  ja: /自動/,
};
const DEFAULT_ON: Record<string, RegExp> = {
  ko: /기본값은 켜짐/,
  en: /On by default/,
  "zh-Hans": /默认开启/,
  "zh-Hant": /預設開啟/,
  ja: /既定ではオン/,
};
const FALLBACK: Record<string, RegExp> = {
  ko: /기존 검색 방식 그대로/,
  en: /existing search method is used/,
  "zh-Hans": /仍使用现有的检索方式/,
  "zh-Hant": /仍使用現有的檢索方式/,
  ja: /これまでの検索方法のまま/,
};

for (const { code } of LANGUAGE_CHOICES) {
  it("describes local retrieval as on for every project, started automatically, in " + code, () => {
    for (const key of KEYS) {
      const value = localRetrievalText(key, code);
      for (const claim of OLD_CLAIMS[code]) expect(value, key).not.toMatch(claim);
    }
    const intro = localRetrievalText("localRetrieval.intro", code);
    const ready = localRetrievalText("localRetrieval.readyNote", code);
    const help = localRetrievalText("localRetrieval.enableHelp", code);
    expect(intro).toMatch(AUTOMATIC[code]);
    expect(help).toMatch(AUTOMATIC[code]);
    expect(help).toMatch(DEFAULT_ON[code]);
    for (const value of [intro, ready]) expect(value).toMatch(FALLBACK[code]);
  });
}
