import { afterEach, expect, it } from "vitest";

import { setLocale, t, uiText, type Locale } from "./locale";
import { messages, type MessageKey } from "./messages";

afterEach(() => setLocale("ko"));

const LANGUAGES: Locale[] = ["ko", "en", "ja", "zh-Hans", "zh-Hant"];

it("shows a notice that was saved in any screen language in the language of the screen", () => {
  setLocale("ko");
  const row = messages["sources.connectFailed"];
  for (const language of LANGUAGES) expect(uiText(row[language]), language).toBe(row.ko);
  setLocale("ja");
  expect(uiText(row.ko)).toBe(row.ja);
});

it("finds the sentences that only the Japanese list has, not only the other four languages", () => {
  setLocale("ko");
  const onlyJapanese = (Object.keys(messages) as MessageKey[]).filter((key) => {
    const row = messages[key];
    return LANGUAGES.every((other) => other === "ja" || row[other] !== row.ja);
  });
  expect(onlyJapanese.length).toBeGreaterThan(1000);
  // On a Korean screen none of them is shown in Japanese any more.
  expect(onlyJapanese.filter((key) => uiText(messages[key].ja) === messages[key].ja)).toEqual([]);
});

it("leaves a text that is not one of the application's own as it is", () => {
  expect(uiText("モデルが書いた答え")).toBe("モデルが書いた答え");
  expect(t("sources.connectFailed", "ja")).toBe(messages["sources.connectFailed"].ja);
});
