// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, it } from "vitest";
import {
  DOCUMENT_TITLES,
  LANGUAGE_CHOICES,
  LOCALE_TAGS,
  browserLanguage,
  isLanguageCode,
} from "./languageChoices";
import { activityActionLabel, activityDisplay } from "./activityDisplay";
import { historyReasons } from "./historyReasons";
import { historyTitle } from "./historyTitle";
import { formatCatalog, message } from "./formatMessages";
import {
  evidenceCount,
  formatDate,
  formatNumber,
  hypothesisLabel,
  localeTag,
  setLocale,
  t,
} from "./locale";
import { messages } from "./messages";
import { formatDuration, missingFor, sceneFor, usageLine } from "../api/researchProgressText";
import { statusText } from "./statusResources";
import { connectionReason } from "./connectionReasons";
import { testOrderTitles } from "./testOrderResources";
import { traceReasonText } from "./traceReasonResources";
import {
  localRetrievalBytes,
  localRetrievalCounts,
  localRetrievalReason,
  localRetrievalText,
} from "./localRetrievalMessages";

const HANGUL = /[가-힣]/;
const KANA_OR_HAN = /[\u3040-\u30ff\u3400-\u9fff]/;
afterEach(() => setLocale("ko"));

it("has Japanese text in every message and every typed sentence, with no Hangul left in it", () => {
  for (const [key, value] of Object.entries(messages)) {
    expect(value.ja.trim(), key).not.toBe("");
    expect(value.ja, key).not.toMatch(HANGUL);
  }
  for (const [key, value] of Object.entries(formatCatalog))
    expect(typeof value.ja, key).toBe("function");
});

it("writes Japanese messages in Japanese, not as copies of the English wording", () => {
  const sameAsEnglish: string[] = [];
  for (const [key, value] of Object.entries(messages)) {
    if (KANA_OR_HAN.test(value.ja) === false && /[a-z]{4,}/.test(value.en)) sameAsEnglish.push(key);
  }
  expect(sameAsEnglish).toEqual([]);
});

it("keeps identifiers, file types and commands that the Korean and English wording share", () => {
  const literal =
    /\b[A-Za-z0-9]+(?:[_.][A-Za-z0-9]+)+\b|\b[A-Z][A-Z0-9]{2,}\b|(?<![A-Za-z])\/[a-z]+\b/g;
  for (const [key, value] of Object.entries(messages)) {
    const shared = (value.en.match(literal) ?? []).filter((token) => value.ko.includes(token));
    for (const token of shared) expect(value.ja, key + " keeps " + token).toContain(token);
  }
});

it("builds the typed Japanese sentences", () => {
  setLocale("ja");
  expect(message("setupStep", 1)).toBe("初期設定 · 1 / 2");
  expect(message("evidenceTotal", "12")).toBe("全 12 件の根拠から選択");
  expect(message("deleteProjectTitle", "Demo")).toBe("プロジェクト「Demo」を完全に削除");
  expect(message("monthDay", 11, 5, "09:30")).toBe("11月5日 09:30");
  expect(message("minutesSeconds", 2, 5)).toBe("2 分 5 秒");
  expect(message("replyFixed", "日本語")).toBe("回答：日本語（固定）");
  expect(message("missingSelection", "thread")).toBe("保存済みの会話の選択を確認してください");
  expect(t("expected.unknown")).toBe("不明");
});

it("picks Japanese from the browser's language tags", () => {
  expect(browserLanguage(["ja-JP", "en"])).toBe("ja");
  expect(browserLanguage(["ja"])).toBe("ja");
  expect(browserLanguage(["en-US", "ja"])).toBe("en");
  expect(browserLanguage(["fr-FR", "ja-JP"])).toBe("ja");
  expect(browserLanguage(["fr-FR"])).toBe("ko");
  expect(isLanguageCode("ja")).toBe(true);
  expect(isLanguageCode("ja-JP")).toBe(false);
});

it("sets the document language and title, and uses the Japanese formats for dates and numbers", () => {
  setLocale("ja");
  expect(document.documentElement.lang).toBe("ja");
  expect(document.title).toBe(DOCUMENT_TITLES.ja);
  expect(localeTag()).toBe("ja-JP");
  expect(LOCALE_TAGS.ja).toBe("ja-JP");
  expect(formatNumber(1234567.5)).toBe(new Intl.NumberFormat("ja-JP").format(1234567.5));
  expect(formatDate("2026-11-05T03:04:05Z")).toBe(
    new Date("2026-11-05T03:04:05Z").toLocaleString("ja-JP"),
  );
  expect(hypothesisLabel(2)).toBe("仮説 2");
  expect(evidenceCount(3)).toBe("根拠 3 件");
  expect(localRetrievalBytes(1_500_000, "ja")).toContain("バイト");
  expect(localRetrievalCounts({ ready: 1, total: 3, pending: 1, failed: 1 }, "ja")).toBe(
    "準備完了 1 / 全 3 · 待機 1 · 失敗 1",
  );
  expect(localRetrievalText("localRetrieval.reasonUnknown", "ja")).toBe("理由は不明");
  expect(localRetrievalReason("EMBEDDING_DISABLED", "ja")).toBe(
    "このプロジェクトでは、ローカルのインデックス化がオフです。",
  );
  expect(LANGUAGE_CHOICES.map((choice) => choice.label)).toContain("日本語");
});

it("puts Japanese fonts first on the Japanese screen only, on the page and on body", () => {
  const css = readFileSync(resolve(process.cwd(), "src/workstation.css"), "utf8");
  const rule = /:root:lang\(ja\),\s*:root:lang\(ja\) body\s*\{([^}]*)\}/.exec(css);
  expect(rule).not.toBeNull();
  const variable = /--bp-typography-family-default:([^;]*);/.exec(rule![1]);
  expect(variable).not.toBeNull();
  expect(/font-family:\s*var\(--bp-typography-family-default\)/.test(rule![1])).toBe(true);
  const faces = variable![1].split(",").map((face) => face.trim().replace(/"/g, ""));
  expect(faces.indexOf("Yu Gothic UI")).toBeGreaterThanOrEqual(0);
  expect(faces.indexOf("Meiryo")).toBeGreaterThan(faces.indexOf("Yu Gothic UI"));
  expect(faces).toEqual(expect.arrayContaining(["Hiragino Sans", "Noto Sans JP"]));
  expect(
    faces.some((face) =>
      /Malgun|Pretendard|Noto Sans KR|Apple SD|YaHei|PingFang|Noto Sans SC/.test(face),
    ),
  ).toBe(false);
  // No other language gets a font rule. (Korean has a line-breaking rule, which sets no font.)
  const fontRules = [...css.matchAll(/([^{}]*:lang\([^{}]*)\{([^}]*)\}/g)].filter((found) =>
    /font/.test(found[2]),
  );
  expect(fontRules).toHaveLength(3);
  expect(rule![1]).not.toMatch(/code-font/);
});

it("never falls back to English, Korean or Chinese for display wording keyed by code", () => {
  setLocale("ja");
  const empty = {
    focus: "",
    constraints: [],
    locators: [],
    hypotheses: [],
    decidedHypotheses: [],
    pendingHypotheses: [],
    sources: [],
  };
  const reads: (() => unknown)[] = [
    () => statusText("labels", "FACT"),
    () => statusText("memoryExclusions", "BUDGET"),
    () => connectionReason("LOGIN_REQUIRED"),
    () => historyReasons.FAILED,
    () => testOrderTitles.READY.hint,
    () => traceReasonText.UNIT.label,
    () => historyTitle({ kind: "REVISION", title: "가설 변경" }),
    () => activityActionLabel("search"),
    () => activityDisplay("가설 3개 중 2개를 검토했습니다."),
    () => formatDuration(65_000),
    () => sceneFor("HOLD", empty, true).title,
    () => usageLine({ usage: { total_tokens: 1200, state: "COMPLETE" } } as never),
  ];
  const seen = reads.map((read) => read());
  for (const value of seen) {
    expect(typeof value).toBe("string");
    expect(value as string).toMatch(/[\u3040-\u30ff\u4e00-\u9fff]/);
    expect(value as string).not.toMatch(HANGUL);
  }
  expect(new Set(seen).size).toBe(seen.length);
  expect(statusText("labels", "FACT")).toBe("事実");
  expect(activityDisplay("가설 3개 중 2개를 검토했습니다.")).toBe(
    "仮説 3 件のうち 2 件をレビューしました。",
  );
  expect(formatDuration(65_000)).toBe("1 分 5 秒");
  expect(statusText("labels", "NOT_A_CODE")).toBeUndefined();
});

it("describes what is still missing in Japanese for each research phase", () => {
  setLocale("ja");
  const facts = {
    focus: "",
    constraints: [],
    locators: [],
    hypotheses: [],
    decidedHypotheses: ["甲"],
    pendingHypotheses: ["乙"],
    sources: [],
  };
  const status = (phase: string, reason = "") =>
    ({ attempt: { phase }, current_result: { terminal_reason: reason } }) as never;
  expect(missingFor(status("HOLD", "HTTP 429"), facts, [], null, null, null)).toBe(
    "モデル呼び出しが、アカウントの利用上限により拒否されました。",
  );
  expect(missingFor(status("HOLD", "unknown"), facts, [], null, null, null)).toBe(
    "モデル呼び出しは、回答が書かれる前に停止しました。",
  );
  expect(missingFor(status("EVIDENCE_FOCUS"), facts, [], 0, null, null)).toContain(
    "質問のための表や箇所は、まだ選ばれていません",
  );
  expect(missingFor(status("EVIDENCE_FOCUS"), facts, ["表 1"], 3, null, null)).toBe(
    "1 か所を確認しました：表 1 · 全 3 か所の一部。",
  );
  expect(missingFor(status("HYPOTHESES"), facts, [], null, 3, 0)).toBe(
    "まず「乙」を比べます。判断はまだ 3 件足りません。",
  );
  expect(missingFor(status("HYPOTHESIS_REVIEW"), facts, [], null, 3, 3)).toBe(
    "「甲」まで比べました。",
  );
  expect(missingFor(status("OTHER"), facts, [], null, null, null)).toBeNull();
});
