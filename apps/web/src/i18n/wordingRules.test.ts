// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, expect, it } from "vitest";
import { activityDisplay } from "./activityDisplay";
import { connectionReason } from "./connectionReasons";
import { formatCatalog, message } from "./formatMessages";
import { historyTitle } from "./historyTitle";
import { localRetrievalReason, localRetrievalReasonMessages } from "./localRetrievalMessages";
import { setLocale } from "./locale";
import { messages } from "./messages";
import { joinSentences, sentenceGap } from "./sentences";

afterEach(() => setLocale("ko"));

// One word for one thing, and no words of the people who built it (04-ideal I-35, I-36).
// "권위": the kind of a source is called "자료 성격" everywhere, on the upload form and in the history alike.
const NOT_IN_KOREAN = ["워크스페이스", "작업공간", "원문 근거", "revision", "결정 객체", "계약에 포함", "권위"];
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

it("calls the kind of a source by one Korean word in the status labels too", () => {
  // The Korean status labels are a table of their own, outside the two lists checked above.
  const labels = readFileSync(resolve(import.meta.dirname, "statusResources.ts"), "utf8");
  expect(labels).toContain("자료 성격이 확인되지 않음");
  expect(labels).not.toContain("권위");
});

// 을/를, 이/가, 은/는, 와/과, 로/으로 depend on the last sound of the word before them. A sentence that puts one
// straight after an inserted name or label is wrong for half of the names ('"2차"을 보관할까요').
const PARTICLE_AFTER_VALUE = /\$\{[^}]+\}["'”’]?(?:을|를|이|가|은|는|와|과|으로|로)(?=[ ,.?!`])/;
// The login button names one of three accounts (ChatGPT, Claude, xAI); read in Korean all three end in a vowel
// sound, so "로" is right for each. A fourth account whose name ends in a consonant needs "으로" and a new sentence.
const PARTICLE_IS_RIGHT_FOR_EVERY_VALUE = new Set(["loginButton"]);

it("puts no Korean particle straight after an inserted value", () => {
  const found: string[] = [];
  for (const [key, value] of Object.entries(formatCatalog)) {
    const source = (value.ko as (...values: never[]) => string).toString();
    if (!PARTICLE_IS_RIGHT_FOR_EVERY_VALUE.has(key) && PARTICLE_AFTER_VALUE.test(source.slice(source.indexOf("=>") + 2))) found.push(key);
  }
  expect(found).toEqual([]);
  for (const account of ["ChatGPT", "Claude", "xAI"]) expect(message("loginButton", account, false)).toBe(account + "로 로그인");
  // the check sees what it is meant to see
  expect(PARTICLE_AFTER_VALUE.test('`프로젝트 "${name}"을 보관할까요?`')).toBe(true);
  expect(PARTICLE_AFTER_VALUE.test('`"${name}" 프로젝트를 보관할까요?`')).toBe(false);
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

// A small label above a heading ("eyebrow") is used only where it says something the heading under it does not.
// One above every heading, repeating the heading or the open tab, is decoration (the audit's slop list, 2).
const SMALL_LABELS: Record<string, string> = {
  "FirstRunSetup.tsx": "the step of the first-run setup (1 / 2), and that an existing setup needs a look",
  "ModelCredentialPanel.tsx": "what the list of account names under it is for",
  "ProjectSessionPages.tsx": "that the page belongs to the review site, which its title does not say",
};

it("puts a small label above a heading only where the label has something of its own to say", () => {
  const used = new Map<string, number>();
  for (const path of screenSources(resolve(import.meta.dirname, ".."))) {
    const count = readFileSync(path, "utf8").split('className="eyebrow"').length - 1;
    if (count > 0) used.set(path.split(/[\\/]/).pop()!, count);
  }
  expect([...used.keys()].sort()).toEqual(Object.keys(SMALL_LABELS).sort());
  expect(used.get("FirstRunSetup.tsx")).toBe(2);
  expect(used.get("ModelCredentialPanel.tsx")).toBe(1);
  expect(used.get("ProjectSessionPages.tsx")).toBe(1);
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

// One word for the unit a person asks questions in, in every screen language. It used to be a session in the
// sidebar, a task in the top bar and a conversation in the menu, so the same thing looked like three.
const LANGUAGES = ["ko", "en", "ja", "zh-Hans", "zh-Hant"] as const;
const CONVERSATION = { ko: "대화", en: "conversation", ja: "会話", "zh-Hans": "对话", "zh-Hant": "對話" } as const;
const CONVERSATION_KEYS = [
  "workspace.newSession", "workspace.loadingSessions", "workspace.untitledSession", "workspace.firstQuestion",
  "workspace.checkingTask", "workspace.selectedTask", "workspace.sessionsCount", "workspace.taskSelectionNeeded",
  "workspace.checkingAccess", "workspace.selectionCheck", "workspace.taskUnavailable", "workspace.loadingSaved",
  "workspace.conversation", "manage.renameThreadTitle", "manage.threadChanged",
  "model.inheritedUnchanged", "model.inheritsProject", "model.saveThread",
  "advanced.task", "advanced.threadGroup", "records.chooseTask", "delta.sameScope",
  "timeline.unavailable", "timeline.recoverAdvice", "historyWorkspace.session",
] as const;
// The words it was called before. They stay only where they mean another thing, and each place says which.
const RETIRED = { ko: /세션/, en: /\b(?:session|task)s?\b/i, ja: /セッション|タスク/, "zh-Hans": /会话|任务/, "zh-Hant": /會話|任務|工作階段/ } as const;
const ANOTHER_THING: Record<string, string> = {
  "advanced.fieldHint": "a session of a field study with its participants",
  RESOURCE_AUTHORITY_CHANGED: "the login session",
  "sources.explicitGrants": "a line of work that was given a source, which is not a conversation",
  "sources.scopeHelp": "the same line of work",
};
const INDEX_JOB = /^(?:localRetrieval\.|EMBEDDING_)/;

it("calls the unit of questions and answers a conversation in every screen language", () => {
  const found: string[] = [];
  for (const key of CONVERSATION_KEYS) {
    const row = messages[key];
    for (const language of LANGUAGES) {
      if (!row[language].toLowerCase().includes(CONVERSATION[language])) found.push(key + " " + language + ": " + row[language]);
    }
    // "작업 공간" is the workspace; any other "작업" here would be the old word for a conversation.
    if (row.ko.replaceAll("작업 공간", "").includes("작업")) found.push(key + " ko: 작업");
  }
  for (const language of LANGUAGES) {
    setLocale(language);
    for (const text of [message("missingSelection", "thread"), message("threadMenu", "A")]) {
      if (!text.toLowerCase().includes(CONVERSATION[language])) found.push(language + ": " + text);
    }
  }
  expect(found).toEqual([]);
});

it("keeps the words a conversation was called before out of every other sentence", () => {
  // Every sentence of the three word lists, by key and language.
  const sentences = new Map<string, string[]>();
  for (const [key, row] of Object.entries(messages)) sentences.set(key, LANGUAGES.map(language => row[language]));
  for (const [key, row] of Object.entries(formatCatalog)) {
    sentences.set(key, LANGUAGES.map(language => writtenPart(row[language] as (...values: never[]) => string)));
  }
  for (const code of Object.keys(localRetrievalReasonMessages)) sentences.set(code, LANGUAGES.map(language => localRetrievalReason(code, language)));
  const retired = (texts: string[]) => LANGUAGES.filter((language, index) => RETIRED[language].test(texts[index]));
  const found: string[] = [];
  for (const [key, texts] of sentences) {
    if (key in ANOTHER_THING || INDEX_JOB.test(key)) continue;
    for (const language of retired(texts)) found.push(key + " " + language);
  }
  expect(found).toEqual([]);
  // Every exception is still in use: a place that stops using the old word leaves the list.
  for (const key of Object.keys(ANOTHER_THING)) expect(retired(sentences.get(key) ?? []), key).not.toEqual([]);
});

it("quotes the two choices for who may use a source by the names they have on the form", () => {
  for (const language of LANGUAGES) {
    const help = messages["sources.scopeHelp"][language];
    expect(help, language).toContain(messages["sources.projectShared"][language]);
    expect(help, language).toContain(messages["sources.explicitGrants"][language]);
  }
});
