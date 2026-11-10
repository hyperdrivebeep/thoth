import { expect, it } from "vitest";
import { LANGUAGE_CHOICES } from "./languageChoices";
import { localRetrievalText } from "./localRetrievalMessages";

/* The three sentences next to the switch said that turning local retrieval on does not change research. It does
   (opt-in, experimental, only when every source is indexed), so each screen language must say so and must not
   promise the opposite. */
const KEYS = ["localRetrieval.intro", "localRetrieval.readyNote", "localRetrieval.enableHelp"] as const;
const FALSE_CLAIMS: Record<string, RegExp[]> = {
  ko: [/검색 정책[^.]*바뀌지/, /검색 결과는 바뀌지/, /선택[^.]*않습니다/],
  en: [/does not (change|select|choose)[^.]*(search|retrieval|evidence)/i, /not change search results/i],
  "zh-Hans": [/不会改变检索/, /不会选择/],
  "zh-Hant": [/不會改變檢索/, /不會選擇/],
  ja: [/検索結果は変わりません/, /選択しません/, /変えません/],
};
const EXPERIMENTAL: Record<string, RegExp> = {
  ko: /실험/, en: /experimental/i, "zh-Hans": /实验/, "zh-Hant": /實驗/, ja: /実験/,
};
const FALLBACK: Record<string, RegExp> = {
  ko: /기존 검색 방식 그대로/, en: /existing search method is used as before/, "zh-Hans": /仍使用现有的检索方式/,
  "zh-Hant": /仍使用現有的檢索方式/, ja: /これまでの検索方法のまま/,
};

for (const { code } of LANGUAGE_CHOICES) {
  it("tells the truth about applying the index to research in " + code, () => {
    for (const key of KEYS) {
      const value = localRetrievalText(key, code);
      for (const claim of FALSE_CLAIMS[code]) expect(value, key).not.toMatch(claim);
    }
    const intro = localRetrievalText("localRetrieval.intro", code);
    const ready = localRetrievalText("localRetrieval.readyNote", code);
    const help = localRetrievalText("localRetrieval.enableHelp", code);
    for (const value of [intro, ready, help]) expect(value).toMatch(EXPERIMENTAL[code]);
    for (const value of [intro, ready]) expect(value).toMatch(FALLBACK[code]);
    expect(help.length).toBeGreaterThan(0);
  });
}

