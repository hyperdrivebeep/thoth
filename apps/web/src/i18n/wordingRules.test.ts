// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, expect, it } from "vitest";
import { activityDisplay } from "./activityDisplay";
import { connectionReason } from "./connectionReasons";
import { formatCatalog } from "./formatMessages";
import { historyTitle } from "./historyTitle";
import { setLocale } from "./locale";
import { messages } from "./messages";
import { joinSentences, sentenceGap } from "./sentences";

afterEach(() => setLocale("ko"));

// One word for one thing, and no words of the people who built it (04-ideal I-35, I-36).
const NOT_IN_KOREAN = ["워크스페이스", "작업공간", "원문 근거", "revision", "결정 객체", "계약에 포함"];
// A dash between a label and its sentence is a habit of machine-written Korean; a colon or a full stop is used.
const DASH = "\u2014";

/** The words of a typed sentence: its source without the parameter list and the inserted values. */
function writtenPart(format: (...values: never[]) => string): string {
  const source = format.toString();
  return source.slice(source.indexOf("=>") + 2).replace(/\$\{[^}]*\}/g, "");
}

it("uses one Korean word for the workspace and for the original text, and no internal terms", () => {
  const found: string[] = [];
  for (const [key, value] of Object.entries(messages)) {
    for (const word of NOT_IN_KOREAN) if (value.ko.includes(word)) found.push(key + ": " + word);
    if (value.ko.includes(DASH)) found.push(key + ": dash");
  }
  for (const [key, value] of Object.entries(formatCatalog)) {
    const written = writtenPart(value.ko as (...values: never[]) => string);
    for (const word of NOT_IN_KOREAN) if (written.includes(word)) found.push(key + ": " + word);
    if (written.includes(DASH)) found.push(key + ": dash");
  }
  expect(found).toEqual([]);
});

// Text between tags that is written in capitals or as a bare English word is not in the translation
// lists, so every language would see it in English. The product name is the one that stays.
const FIXED_ENGLISH = />\s*([A-Z][A-Za-z]+(?: [A-Za-z]+)*)\s*</g;
const ALLOWED = new Set(["THOTH"]);

function screenSources(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap(entry => {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) return screenSources(path);
    return entry.name.endsWith(".tsx") && !entry.name.includes(".test.") ? [path] : [];
  });
}

it("has no English wording written straight into a screen", () => {
  const found: string[] = [];
  for (const path of screenSources(resolve(import.meta.dirname, ".."))) {
    for (const match of readFileSync(path, "utf8").matchAll(FIXED_ENGLISH)) {
      // "> Promise<" and "> rpc<" are TypeScript, not screen text.
      const typeScript = /^(Promise|rpc)$/.test(match[1]);
      if (!ALLOWED.has(match[1]) && !typeScript) found.push(path.split(/[\\/]/).pop() + ": " + match[1]);
    }
  }
  expect(found).toEqual([]);
});

it("names the workspace and the project list in every screen language", () => {
  for (const key of ["brand.tagline", "nav.projects", "nav.workspaceRoot"] as const) {
    const row = messages[key];
    expect(new Set([row.ko, row.en, row.ja, row["zh-Hans"], row["zh-Hant"]]).size, key).toBeGreaterThanOrEqual(4);
    expect(row.ko, key).toMatch(/[가-힣]/);
  }
});

it("shows the history title the server wrote, said plainly in Korean too", () => {
  expect(historyTitle({ kind: "REVISION", title: "결정 객체 변경" })).toBe("결정 변경");
  expect(historyTitle({ kind: "REVISION", title: "가설 변경" })).toBe("가설 변경");
  setLocale("en");
  expect(historyTitle({ kind: "REVISION", title: "결정 객체 변경" })).toBe("Decision updated");
});

it("translates the changed-source line in the wording the server writes now and in the one saved before", () => {
  setLocale("en");
  const english = "2 sources with changed currentness need rechecking.";
  expect(activityDisplay("최신 상태가 달라진 자료 2곳은 다시 확인해야 합니다.")).toBe(english);
  expect(activityDisplay("현재성이 달라진 자료 2곳은 다시 확인해야 합니다.")).toBe(english);
  setLocale("ja");
  expect(activityDisplay("최신 상태가 달라진 자료 2곳은 다시 확인해야 합니다.")).not.toMatch(/[가-힣]/);
});

it("puts a space between sentences in Korean and English and none in Japanese and Chinese", () => {
  expect(joinSentences(["첫 문장입니다.", "", null, " 둘째 문장입니다."])).toBe("첫 문장입니다. 둘째 문장입니다.");
  setLocale("en");
  expect(joinSentences(["One.", "Two."])).toBe("One. Two.");
  for (const code of ["ja", "zh-Hans", "zh-Hant"] as const) {
    setLocale(code);
    expect(sentenceGap(), code).toBe("");
    expect(joinSentences(["一。", " 二。"]), code).toBe("一。二。");
  }
});

it("says in every screen language why a key was not saved, without the reason code", () => {
  for (const code of ["MODEL_CREDENTIAL_STORE_UNREADABLE", "MODEL_CREDENTIAL_STORE_UNWRITABLE"]) {
    const texts = (["ko", "en", "ja", "zh-Hans", "zh-Hant"] as const).map(language => {
      setLocale(language);
      return connectionReason(code) ?? "";
    });
    expect(new Set(texts).size, code).toBe(5);
    for (const text of texts) {
      expect(text.length, code).toBeGreaterThan(20);
      expect(text, code).not.toContain("MODEL_CREDENTIAL");
    }
  }
  setLocale("ko");
  expect(connectionReason("MODEL_CREDENTIAL_STORE_UNREADABLE")).toContain("새 키를 저장하지 않았습니다");
});
