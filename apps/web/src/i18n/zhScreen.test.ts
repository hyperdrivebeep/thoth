// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
  applyDocumentLocale,
  evidenceCount,
  formatDate,
  formatNumber,
  hypothesisLabel,
  localeTag,
  setLocale,
  t,
  type Locale,
} from "./locale";
import { messages } from "./messages";
import { formatDuration, missingFor, sceneFor, usageLine } from "../api/researchProgressText";
import { statusText } from "./statusResources";
import { connectionReason } from "./connectionReasons";
import { testOrderTitles } from "./testOrderResources";
import { traceReasonText } from "./traceReasonResources";

const CODES = LANGUAGE_CHOICES.map((choice) => choice.code);
afterEach(() => setLocale("ko"));

it("has every message and every typed sentence in every screen language", () => {
  for (const [key, value] of Object.entries(messages)) {
    for (const code of CODES)
      expect(typeof value[code] === "string" && value[code].trim() !== "", key + " " + code).toBe(
        true,
      );
  }
  for (const [key, value] of Object.entries(formatCatalog)) {
    for (const code of CODES) expect(typeof value[code], key + " " + code).toBe("function");
  }
});

it("keeps identifiers, file types and commands that the Korean and English wording share", () => {
  const literal =
    /\b[A-Za-z0-9]+(?:[_.][A-Za-z0-9]+)+\b|\b[A-Z][A-Z0-9]{2,}\b|(?<![A-Za-z])\/[a-z]+\b/g;
  for (const [key, value] of Object.entries(messages)) {
    const shared = (value.en.match(literal) ?? []).filter((token) => value.ko.includes(token));
    for (const token of shared) expect(value["zh-Hans"], key + " keeps " + token).toContain(token);
  }
});

it("does not leave Korean in the Chinese sentences", () => {
  setLocale("zh-Hans");
  expect(message("setupStep", 1)).toBe("初始设置 · 1 / 2");
  expect(message("evidenceTotal", "12")).toBe("从共 12 条证据中选出");
  expect(message("deleteProjectTitle", "Demo")).toBe("永久删除项目“Demo”");
  expect(message("monthDay", 11, 5, "09:30")).toBe("11月5日 09:30");
  expect(message("notNumber", "abc")).toBe("abc（不是数字）");
  expect(message("minutesSeconds", 2, 5)).toBe("2 分 5 秒");
  expect(t("expected.unknown")).toBe("未知");
});

it("picks the language of each screen from the browser's language tags, with Traditional tags choosing the Traditional screen", () => {
  expect(browserLanguage(["zh-CN", "en"])).toBe("zh-Hans");
  expect(browserLanguage(["zh-Hans-CN"])).toBe("zh-Hans");
  expect(browserLanguage(["zh"])).toBe("zh-Hans");
  expect(browserLanguage(["zh-SG"])).toBe("zh-Hans");
  expect(browserLanguage(["zh-TW", "zh-HK", "en-US"])).toBe("zh-Hant");
  expect(browserLanguage(["zh-MO"])).toBe("zh-Hant");
  expect(browserLanguage(["zh-Hant", "ko-KR"])).toBe("zh-Hant");
  expect(browserLanguage(["zh-Hans-HK"])).toBe("zh-Hans");
  expect(browserLanguage(["yue-HK", "ko-KR"])).toBe("ko");
  expect(browserLanguage(["fr-FR"])).toBe("ko");
  expect(browserLanguage(["en-GB", "zh-CN"])).toBe("en");
  expect(browserLanguage(["ko"])).toBe("ko");
  expect(isLanguageCode("zh-Hans")).toBe(true);
  expect(isLanguageCode("zh")).toBe(false);
});

it("sets the document language and title, and uses the Chinese formats for dates and numbers", () => {
  setLocale("zh-Hans");
  expect(document.documentElement.lang).toBe("zh-Hans");
  expect(document.title).toBe(DOCUMENT_TITLES["zh-Hans"]);
  expect(localeTag()).toBe("zh-CN");
  expect(formatNumber(1234567.5)).toBe(new Intl.NumberFormat("zh-CN").format(1234567.5));
  expect(formatDate("2026-11-05T03:04:05Z")).toBe(
    new Date("2026-11-05T03:04:05Z").toLocaleString("zh-CN"),
  );
  expect(hypothesisLabel(2)).toBe("假设 2");
  expect(evidenceCount(1)).toBe("1 条证据");
  expect(evidenceCount(3)).toBe("3 条证据");
  for (const code of CODES) {
    setLocale(code);
    expect(document.documentElement.lang).toBe(code);
    expect(localeTag()).toBe(LOCALE_TAGS[code]);
  }
  applyDocumentLocale("ko");
  expect(document.title).toBe(DOCUMENT_TITLES.ko);
});

it("puts Chinese fonts first on the Chinese screen only, on the page and on body where Blueprint sets its own font", () => {
  const workstationCss = readFileSync(resolve(process.cwd(), "src/workstation.css"), "utf8");
  const rule = /:root:lang\(zh-Hans\),\s*:root:lang\(zh-Hans\) body\s*\{([^}]*)\}/.exec(
    workstationCss,
  );
  expect(rule).not.toBeNull();
  const variable = /--bp-typography-family-default:([^;]*);/.exec(rule![1]);
  expect(variable).not.toBeNull();
  expect(/font-family:\s*var\(--bp-typography-family-default\)/.test(rule![1])).toBe(true);
  const faces = variable![1].split(",").map((face) => face.trim().replace(/"/g, ""));
  expect(faces.indexOf("Microsoft YaHei UI")).toBeGreaterThanOrEqual(0);
  expect(faces.indexOf("PingFang SC")).toBeGreaterThan(faces.indexOf("Microsoft YaHei UI"));
  expect(faces).toContain("Noto Sans SC");
  expect(faces.some((face) => /Malgun|Pretendard|Noto Sans KR|Apple SD/.test(face))).toBe(false);
  // No other language gets a font rule. (Korean has a line-breaking rule, which sets no font.)
  const fontRules = [...workstationCss.matchAll(/([^{}]*:lang\([^{}]*)\{([^}]*)\}/g)].filter(
    (found) => /font/.test(found[2]),
  );
  expect(fontRules).toHaveLength(3);
  expect(rule![1]).not.toMatch(/code-font/);
});

it("never falls back to English or Korean for display wording keyed by code", () => {
  const checks: [Locale, () => unknown][] = [
    ["zh-Hans", () => statusText("labels", "FACT")],
    ["zh-Hans", () => statusText("memoryExclusions", "BUDGET")],
    ["zh-Hans", () => connectionReason("LOGIN_REQUIRED")],
    ["zh-Hans", () => historyReasons.FAILED],
    ["zh-Hans", () => testOrderTitles.READY.hint],
    ["zh-Hans", () => traceReasonText.UNIT.label],
    ["zh-Hans", () => historyTitle({ kind: "REVISION", title: "가설 변경" })],
    ["zh-Hans", () => activityActionLabel("search")],
    ["zh-Hans", () => activityDisplay("가설 3개 중 2개를 검토했습니다.")],
    ["zh-Hans", () => formatDuration(65_000)],
    [
      "zh-Hans",
      () =>
        sceneFor(
          "HOLD",
          {
            focus: "",
            constraints: [],
            locators: [],
            hypotheses: [],
            decidedHypotheses: [],
            pendingHypotheses: [],
            sources: [],
          },
          true,
        ).title,
    ],
    ["zh-Hans", () => usageLine({ usage: { total_tokens: 1200, state: "COMPLETE" } } as never)],
  ];
  const seen: unknown[] = [];
  for (const [locale, read] of checks) {
    setLocale(locale);
    const value = read();
    expect(typeof value).toBe("string");
    expect(value as string).toMatch(/[\u4e00-\u9fff]/);
    expect(value as string).not.toMatch(/[가-힣]/);
    seen.push(value);
  }
  expect(new Set(seen).size).toBe(seen.length);
  setLocale("zh-Hans");
  expect(statusText("labels", "FACT")).toBe("事实");
  expect(activityDisplay("가설 3개 중 2개를 검토했습니다.")).toBe("已审查 3 个假设中的 2 个。");
  expect(formatDuration(65_000)).toBe("1 分 5 秒");
  expect(statusText("labels", "NOT_A_CODE")).toBeUndefined();
});
it("describes what is still missing in Chinese for each research phase", () => {
  setLocale("zh-Hans");
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
    "模型调用被账户的使用上限拒绝。",
  );
  expect(missingFor(status("HOLD", "AUTH_REQUIRED"), facts, [], null, null, null)).toBe(
    "模型账户的身份验证被拒绝。",
  );
  expect(missingFor(status("HOLD", "unknown"), facts, [], null, null, null)).toBe(
    "模型调用在撰写回答之前停止了。",
  );
  expect(missingFor(status("EVIDENCE_FOCUS"), facts, [], 0, null, null)).toContain(
    "尚未为问题选出任何表格或段落",
  );
  expect(missingFor(status("EVIDENCE_FOCUS"), facts, ["表 1"], 3, null, null)).toBe(
    "已检查 1 处位置：表 1 · 共 3 处位置中的一部分。",
  );
  expect(missingFor(status("HYPOTHESES"), facts, [], null, 3, 0)).toBe(
    "将先比较“乙”。还缺少 3 项决定。",
  );
  expect(missingFor(status("HYPOTHESIS_REVIEW"), facts, [], null, 3, 3)).toBe("已比较到“甲”。");
  expect(missingFor(status("CONNECTED_SOURCES"), facts, [], null, null, null)).toBe(
    "正在打开已连接的资料的列表；回答尚未撰写。",
  );
  expect(missingFor(status("OTHER"), facts, [], null, null, null)).toBeNull();
});
