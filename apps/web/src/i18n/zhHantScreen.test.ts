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

const HANT: Locale = "zh-Hant";
afterEach(() => setLocale("ko"));

// Every character of the Simplified Chinese column that is not a valid Traditional form (OpenCC STCharacters minus the Traditional forms); none may be left in the Traditional column.
const SIMPLIFIED_ONLY = new Set([
  ..."与专业丢两个为么义争产仅从仓们众会传体侧储关内写冲决况冻减凭则创删别务动区单历压参发变号吗启员响围图场坏声处备复够头夹实审对寻导将尝尽属带并库应开异张强归当录径忆态总户执扩护报拟择损换搁数断无时显暂术机杂权条来构标样档检气汇没测浏滚滞满点状独环现电盖码础碍离称竞筛签简类约级纪纳纹线组细织终经绑结绘络绝统继绪续缓编缩网联脑脚节获营补装见观规视览触计订认议记许论设访证评识诊词试话询该详语误说请读谁调谱负责败账费资转轮软载较辑输辞边达过运还这进远连迟选递释钟钥钮链销错键长闭问间阅阈阶际陈险随隐雾静页项顺须预领频题额风驳验骤",
]);
// Wording the OpenCC conversion gets wrong for Taiwan usage, or that is mainland usage; corrected when the column was made.
const NOT_TAIWAN = [
  "許可權",
  "訪問",
  "響應",
  "賬",
  "當前",
  "型別",
  "宣告",
  "只讀",
  "身份",
  "憑據",
  "擴充套件",
  "軟體包",
  "倉庫",
  "控制檯",
  "瞭",
  "識別符",
  "示例",
  "計劃",
  "恢復",
  "配置檔案",
  "工作程序",
];

const everyTraditionalText = (): [string, string][] => [
  ...Object.entries(messages).map(([key, value]) => [key, value[HANT]] as [string, string]),
];

it("has every message and every typed sentence in the Traditional Chinese column", () => {
  for (const [key, text] of everyTraditionalText()) expect(text.trim() !== "", key).toBe(true);
  for (const [key, value] of Object.entries(formatCatalog))
    expect(typeof value[HANT], key).toBe("function");
  expect(LANGUAGE_CHOICES.map((choice) => choice.code)).toContain(HANT);
});

it("keeps identifiers, file types and commands that the Korean and English wording share", () => {
  const literal =
    /\b[A-Za-z0-9]+(?:[_.][A-Za-z0-9]+)+\b|\b[A-Z][A-Z0-9]{2,}\b|(?<![A-Za-z])\/[a-z]+\b/g;
  for (const [key, value] of Object.entries(messages)) {
    const shared = (value.en.match(literal) ?? []).filter((token) => value.ko.includes(token));
    for (const token of shared) expect(value[HANT], key + " keeps " + token).toContain(token);
  }
});

it("has the same placeholders in both Chinese columns and no Korean or Simplified-only character in the Traditional one", () => {
  for (const [key, text] of everyTraditionalText()) {
    expect(text, key).not.toMatch(/[가-힣]/);
    for (const char of text) expect(SIMPLIFIED_ONLY.has(char), key + " " + char).toBe(false);
    expect(text.match(/\{[^}]*\}|%[sd]/g) ?? [], key).toEqual(
      messages[key as keyof typeof messages]["zh-Hans"].match(/\{[^}]*\}|%[sd]/g) ?? [],
    );
  }
});

it("uses Taiwan wording where the conversion tool or mainland usage would not", () => {
  const all = everyTraditionalText()
    .map(([, text]) => text)
    .join("\n");
  for (const term of NOT_TAIWAN) expect(all.includes(term), term).toBe(false);
  setLocale(HANT);
  expect(t("usage.memoryExpansion")).toBe("記憶檢索詞擴充");
  expect(t("history.itemAbsent")).toBe("項目不存在");
  expect(t("restore.impact")).toBe("對關聯項目的影響");
  expect(t("delta.sameScope")).toBe("請從同一專案和同一對話中選擇兩個回答。");
  expect(t("closure.basis")).toBe("佐證文件");
  expect(t("review.unsupported")).toBe("未得到支持");
  expect(t("credential.keyOnly")).toBe("不支援帳戶登入。請獲取並登記 API 金鑰。");
  expect(t("expected.unknown")).toBe("未知");
});

it("writes typed sentences in Traditional Chinese", () => {
  setLocale(HANT);
  expect(message("setupStep", 1)).toBe("初始設定 · 1 / 2");
  expect(message("evidenceTotal", "12")).toBe("從共 12 條證據中選出");
  expect(message("deleteProjectTitle", "Demo")).toBe("永久刪除專案“Demo”");
  expect(message("monthDay", 11, 5, "09:30")).toBe("11月5日 09:30");
  expect(message("notNumber", "abc")).toBe("abc（不是數字）");
  expect(message("minutesSeconds", 2, 5)).toBe("2 分 5 秒");
});

it("picks the Traditional screen from Traditional browser tags and keeps Simplified and Cantonese tags where they were", () => {
  for (const tag of ["zh-TW", "zh-HK", "zh-MO", "zh-Hant", "zh-Hant-TW", "zh-Hant-CN", "ZH-tw"])
    expect(browserLanguage([tag, "en"]), tag).toBe(HANT);
  for (const tag of ["zh", "zh-CN", "zh-SG", "zh-Hans", "zh-Hans-CN", "zh-Hans-HK", "zh-Hans-TW"])
    expect(browserLanguage([tag, "en"]), tag).toBe("zh-Hans");
  expect(browserLanguage(["yue", "en"])).toBe("en");
  expect(browserLanguage(["yue-HK", "ko-KR"])).toBe("ko");
  expect(browserLanguage(["fr-FR", "zh-TW"])).toBe(HANT);
  expect(browserLanguage(["en-US", "zh-TW"])).toBe("en");
  expect(isLanguageCode(HANT)).toBe(true);
  expect(isLanguageCode("zh-TW")).toBe(false);
  expect(isLanguageCode("zh-hant")).toBe(false);
});

it("sets the document language and title, and uses the Taiwan formats for dates and numbers", () => {
  setLocale(HANT);
  expect(document.documentElement.lang).toBe("zh-Hant");
  expect(document.title).toBe(DOCUMENT_TITLES[HANT]);
  expect(DOCUMENT_TITLES[HANT]).toBe("THOTH · 研究工作區");
  expect(localeTag()).toBe("zh-TW");
  expect(LOCALE_TAGS[HANT]).toBe("zh-TW");
  expect(formatNumber(1234567.5)).toBe(new Intl.NumberFormat("zh-TW").format(1234567.5));
  expect(formatDate("2026-11-05T03:04:05Z")).toBe(
    new Date("2026-11-05T03:04:05Z").toLocaleString("zh-TW"),
  );
  expect(hypothesisLabel(2)).toBe("假設 2");
  expect(evidenceCount(1)).toBe("1 條證據");
  expect(evidenceCount(3)).toBe("3 條證據");
});

it("puts Traditional Chinese fonts first on the Traditional screen only, on the page and on body", () => {
  const css = readFileSync(resolve(process.cwd(), "src/workstation.css"), "utf8");
  const rule = /:root:lang\(zh-Hant\),\s*:root:lang\(zh-Hant\) body\s*\{([^}]*)\}/.exec(css);
  expect(rule).not.toBeNull();
  const variable = /--bp-typography-family-default:([^;]*);/.exec(rule![1]);
  expect(variable).not.toBeNull();
  expect(/font-family:\s*var\(--bp-typography-family-default\)/.test(rule![1])).toBe(true);
  const faces = variable![1].split(",").map((face) => face.trim().replace(/"/g, ""));
  expect(faces.indexOf("Microsoft JhengHei UI")).toBeGreaterThanOrEqual(0);
  expect(faces.indexOf("PingFang TC")).toBeGreaterThan(faces.indexOf("Microsoft JhengHei UI"));
  expect(faces).toEqual(expect.arrayContaining(["Noto Sans TC", "Source Han Sans TC"]));
  expect(
    faces.some((face) =>
      /Malgun|Pretendard|Noto Sans KR|Apple SD|YaHei|PingFang SC|Noto Sans SC|Yu Gothic|Meiryo|Noto Sans JP/.test(
        face,
      ),
    ),
  ).toBe(false);
  // No other language gets a font rule. (Korean has a line-breaking rule, which sets no font.)
  const fontRules = [...css.matchAll(/([^{}]*:lang\([^{}]*)\{([^}]*)\}/g)].filter((found) =>
    /font/.test(found[2]),
  );
  expect(fontRules).toHaveLength(3);
  expect(rule![1]).not.toMatch(/code-font/);
});

it("never falls back to English, Korean or Simplified Chinese for display wording keyed by code", () => {
  setLocale(HANT);
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
    expect(value as string).toMatch(/[\u4e00-\u9fff]/);
    expect(value as string).not.toMatch(/[가-힣]/);
    for (const char of value as string)
      expect(SIMPLIFIED_ONLY.has(char), value + " " + char).toBe(false);
  }
  expect(new Set(seen).size).toBe(seen.length);
  expect(statusText("labels", "FACT")).toBe("事實");
  expect(statusText("labels", "SUPPORTED")).toBe("得到支持");
  expect(activityDisplay("가설 3개 중 2개를 검토했습니다.")).toBe("已審查 3 個假設中的 2 個。");
  expect(formatDuration(65_000)).toBe("1 分 5 秒");
  expect(statusText("labels", "NOT_A_CODE")).toBeUndefined();
});

it("describes what is still missing in Traditional Chinese for each research phase", () => {
  setLocale(HANT);
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
    "模型呼叫被帳戶的使用上限拒絕。",
  );
  expect(missingFor(status("HOLD", "AUTH_REQUIRED"), facts, [], null, null, null)).toBe(
    "模型帳戶的身分驗證被拒絕。",
  );
  expect(missingFor(status("HOLD", "unknown"), facts, [], null, null, null)).toBe(
    "模型呼叫在撰寫回答之前停止了。",
  );
  expect(missingFor(status("EVIDENCE_FOCUS"), facts, [], 0, null, null)).toContain(
    "尚未為問題選出任何表格或段落",
  );
  expect(missingFor(status("EVIDENCE_FOCUS"), facts, ["表 1"], 3, null, null)).toBe(
    "已檢查 1 處位置：表 1 · 共 3 處位置中的一部分。",
  );
  expect(missingFor(status("HYPOTHESES"), facts, [], null, 3, 0)).toBe(
    "將先比較“乙”。還缺少 3 項決定。",
  );
  expect(missingFor(status("HYPOTHESIS_REVIEW"), facts, [], null, 3, 3)).toBe("已比較到“甲”。");
  expect(missingFor(status("CONNECTED_SOURCES"), facts, [], null, null, null)).toBe(
    "正在開啟已連線的資料的列表；回答尚未撰寫。",
  );
  expect(missingFor(status("OTHER"), facts, [], null, null, null)).toBeNull();
});
