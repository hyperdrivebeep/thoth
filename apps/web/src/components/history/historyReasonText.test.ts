import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { describeHistoryReason, describeHistoryReasons, isKnownHistoryReason } from "./historyReasonText";

const source = (path: string) => readFileSync(new URL("../../../../../src/thoth/" + path, import.meta.url), "utf8");
const RAW_CODE = /[A-Z]{3,}_[A-Z_]{3,}/;
// Lifecycle states that appear on the same lines as reasons but are not reasons themselves.
const NOT_REASONS = new Set(["REVIEW_REQUIRED", "INVALIDATED", "UNAVAILABLE", "UNKNOWN_REASON", "RECORDED", "UNKNOWN_BASIS_STATE"]);

/** Codes a screen can receive in reason_codes, read from where the backend writes them:
 *  research_freshness.py (currentness reasons), research_coverage.py (coverage reasons via reasons.append),
 *  research_followup_projection.py (review list and comparison), history_projection.py (RESULT_SUPERSEDED),
 *  research_followup.py (comparison input error). */
function backendCodes(): string[] {
  const found = new Set<string>();
  const files: [string, RegExp][] = [
    ["application/services/research_freshness.py", /reasons=\(\s*"([A-Z_]+)"|currentness\.reasons, "([A-Z_]+)"/g],
    ["application/services/research_coverage.py", /reasons\.append\("([A-Z_]+)"\)/g],
    ["application/services/research_followup_projection.py", /reason_codes=\(\s*"([A-Z_]+)"|\("([A-Z_]+)",\)\)/g],
    ["application/services/history_projection.py", /\*currentness\.reasons, "([A-Z_]+)"/g],
  ];
  for (const [path, pattern] of files) {
    for (const match of source(path).matchAll(pattern)) {
      const code = match[1] ?? match[2];
      if (code && !NOT_REASONS.has(code)) found.add(code);
    }
  }
  return [...found];
}

describe("history reason wording", () => {
  it("has a sentence for every reason code the backend writes", () => {
    const codes = backendCodes();
    expect(codes.length).toBeGreaterThan(25);
    for (const code of codes) {
      expect(isKnownHistoryReason(code), code).toBe(true);
      expect(describeHistoryReason(code), code).not.toMatch(RAW_CODE);
    }
  });

  it("says something general for an unknown code and shows research-written text as it is", () => {
    expect(describeHistoryReason("SOMETHING_NEW_HAPPENED")).toBe("기록된 다른 이유가 있습니다.");
    expect(describeHistoryReason("표본이 작아 결론을 보류했습니다")).toBe("표본이 작아 결론을 보류했습니다");
  });

  it("lists each wording once", () => {
    expect(describeHistoryReasons(["SOMETHING_NEW_HAPPENED", "ANOTHER_NEW_ONE", "SOURCE_BASIS_CHANGED"])).toEqual(["기록된 다른 이유가 있습니다.", "답변이 쓴 자료가 이후 바뀌었습니다."]);
  });
});
