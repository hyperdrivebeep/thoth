import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { TraceView, Verdict } from "../api/trace";
import { REASON_CODE_TAGS, REASON_TAG_TEXT, reasonTags } from "./traceReasonTags";

const view = (links: { from_id: string; to_id: string; relation: string }[] = []) =>
  ({ links: links.map((link, index) => ({ link_id: "L" + index, ...link })), items: [], rules: [], results: [], verdicts: [] }) as unknown as TraceView;
const LINKED = view([{ from_id: "C1", to_id: "T1", relation: "VERIFIED_BY" }]);

function verdict(kind: "CRITERION" | "REQUIREMENT", state: string, computed: string[], extra: Record<string, unknown> = {}): Verdict {
  return {
    subject_kind: kind, subject_id: "C1", state, reasons: { computed, human: [] },
    selection: kind === "CRITERION" ? { policy: { name: "LATEST_PER_CONDITION", version: 1 }, candidates: [], chosen: [], excluded: [] } : null,
    currentness: { state: "CURRENT", changed_dependencies: [] }, ...extra,
  } as unknown as Verdict;
}
const ids = (kind: "CRITERION" | "REQUIREMENT", item: Verdict, v: TraceView = LINKED) => reasonTags(kind, item, v).map(tag => tag.id);

describe("reason tags of a criterion", () => {
  it("says untested when a test is linked and no result came, and unlinked when no test is linked", () => {
    const none = verdict("CRITERION", "HOLD_NO_RESULT", ["NO_RESULT:weather=fog"]);
    expect(ids("CRITERION", none)).toEqual(["UNTESTED"]);
    expect(ids("CRITERION", none, view())).toEqual(["UNLINKED"]);
    expect(ids("CRITERION", none, view([{ from_id: "OTHER", to_id: "T1", relation: "VERIFIED_BY" }]))).toEqual(["UNLINKED"]);
    expect(ids("CRITERION", none, view([{ from_id: "C1", to_id: "R1", relation: "REFINES" }]))).toEqual(["UNLINKED"]);
  });

  it("says condition mismatch when results exist but every one was set aside for its condition", () => {
    const mismatch = verdict("CRITERION", "HOLD_NO_RESULT", ["NO_RESULT:weather=rain"], {
      selection: { policy: { name: "LATEST_PER_CONDITION", version: 1 }, candidates: [{ result_id: "R1", result_revision: 1 }], chosen: [], excluded: [{ result_id: "R1", result_revision: 1, reason: "CONDITION_MISMATCH" }] },
    });
    expect(ids("CRITERION", mismatch)).toEqual(["CONDITION_MISMATCH"]);
  });

  it.each([
    ["UNIT_MISMATCH:R1:percent!=ratio", "UNIT"],
    ["DENOMINATOR_NOT_POSITIVE:R1", "DENOMINATOR"],
    ["VALUE_DISAGREES_WITH_COUNTS:R1", "DENOMINATOR"],
    ["VALUE_NOT_A_NUMBER:R1", "VALUE"],
    ["VALUE_MISSING:R1", "VALUE"],
    ["VALUE_OUT_OF_RANGE:R1", "VALUE"],
  ])("maps %s to %s", (code, tag) => {
    expect(ids("CRITERION", verdict("CRITERION", "HOLD_INVALID_RESULT", [code]))).toEqual([tag]);
  });

  it("says no rule", () => {
    expect(ids("CRITERION", verdict("CRITERION", "HOLD_NO_RULE", ["NO_RULE"]))).toEqual(["NO_RULE"]);
  });

  it("calls a miss valid only when nothing else made a result unusable", () => {
    const miss = "THRESHOLD_NOT_MET:R1:value=0.80 need >= 0.90 ratio";
    expect(ids("CRITERION", verdict("CRITERION", "FAIL_COMPUTED", [miss]))).toEqual(["VALID_MISS"]);
    expect(ids("CRITERION", verdict("CRITERION", "FAIL_COMPUTED", [miss, miss]))).toEqual(["VALID_MISS"]);
    expect(ids("CRITERION", verdict("CRITERION", "FAIL_COMPUTED", [miss, "UNIT_MISMATCH:R2:percent!=ratio"]))).toEqual(["UNIT"]);
  });

  it("can carry several tags on one line, in a fixed order", () => {
    const both = verdict("CRITERION", "HOLD_INVALID_RESULT", ["VALUE_MISSING:R1", "UNIT_MISMATCH:R2:x!=y", "DENOMINATOR_NOT_POSITIVE:R3"]);
    expect(ids("CRITERION", both)).toEqual(["UNIT", "DENOMINATOR", "VALUE"]);
  });

  it("makes no tag for a met line or for a code it does not know", () => {
    expect(ids("CRITERION", verdict("CRITERION", "PASS_COMPUTED", []))).toEqual([]);
    expect(ids("CRITERION", verdict("CRITERION", "HOLD_INVALID_RESULT", ["SOMETHING_NEW:R1"]))).toEqual([]);
    expect(ids("CRITERION", verdict("CRITERION", "FAIL_COMPUTED", ["SOMETHING_NEW"]))).toEqual([]);
  });
});

describe("reason tags of a requirement and of a changed basis", () => {
  it.each(["HOLD_INCOMPLETE", "FAIL_WITH_INCOMPLETE_COVERAGE", "HOLD_NO_CRITERIA"])("marks %s as required criteria not complete", state => {
    expect(ids("REQUIREMENT", verdict("REQUIREMENT", state, ["C1:HOLD_NO_RESULT"]))).toEqual(["REQUIRED_INCOMPLETE"]);
  });

  it("marks a requirement with no required criteria", () => {
    expect(ids("REQUIREMENT", verdict("REQUIREMENT", "HOLD_NO_CRITERIA", ["NO_REQUIRED_CRITERIA"]))).toEqual(["REQUIRED_INCOMPLETE"]);
  });

  it("gives nothing to a met or plainly failed requirement", () => {
    expect(ids("REQUIREMENT", verdict("REQUIREMENT", "PASS", []))).toEqual([]);
    expect(ids("REQUIREMENT", verdict("REQUIREMENT", "FAIL", ["C1:FAIL_COMPUTED"]))).toEqual([]);
  });

  it("adds the changed-basis tag to either kind, and only on a stale line", () => {
    const stale = { currentness: { state: "STALE_BASIS", changed_dependencies: [] } };
    expect(ids("CRITERION", verdict("CRITERION", "PASS_COMPUTED", [], stale))).toEqual(["BASIS_CHANGED"]);
    expect(ids("REQUIREMENT", verdict("REQUIREMENT", "FAIL_WITH_INCOMPLETE_COVERAGE", ["C1:HOLD_NO_RESULT"], stale))).toEqual(["REQUIRED_INCOMPLETE", "BASIS_CHANGED"]);
    expect(ids("CRITERION", verdict("CRITERION", "FAIL_COMPUTED", ["THRESHOLD_NOT_MET:R1:value=0.8 need >= 0.9 ratio"], stale))).toEqual(["VALID_MISS", "BASIS_CHANGED"]);
  });
});

describe("tag wording", () => {
  const BANNED = /재시험 필수|원인은|기준을 낮추|통과 가능|승인됨|종결|해결|면제|AI 추천/;
  it("is short Korean with a one-line hint, no codes and no forbidden phrases", () => {
    const entries = Object.values(REASON_TAG_TEXT);
    expect(entries).toHaveLength(10);
    for (const { label, hint } of entries) {
      expect(label.length).toBeLessThanOrEqual(12);
      expect(hint).not.toContain("\n");
      for (const text of [label, hint]) {
        expect(text).not.toMatch(BANNED);
        expect(text).not.toMatch(/HOLD_|FAIL_|PASS_|[A-Za-z]+_[A-Za-z]+/);
        expect(text).toMatch(/[가-힣]/);
      }
    }
  });
});

/** The reason codes the server can give a verdict, read from where it builds them: `f"CODE:{...}"` and `("CODE",)`. A code built another way is not seen. */
function serverReasonCodes(text: string): string[] {
  const found = [...text.matchAll(/f"([A-Z][A-Z_]+):\{/g), ...text.matchAll(/(?:computed=|else )\(\s*"([A-Z][A-Z_]+)",\s*\)/g)].map(match => match[1]);
  return [...new Set(found)].sort();
}
// The verdicts are computed in verification_trace_compute.py; verification_trace.py keeps the service around it.
const traceSource = () => ["verification_trace.py", "verification_trace_compute.py"]
  .map(name => readFileSync(new URL("../../../../src/thoth/application/services/" + name, import.meta.url), "utf8")).join("\n");

describe("reason codes against the server", () => {
  it("reads the ten codes the server gives today", () => {
    expect(serverReasonCodes(traceSource())).toEqual([
      "DENOMINATOR_NOT_POSITIVE", "NO_REQUIRED_CRITERIA", "NO_RESULT", "NO_RULE", "THRESHOLD_NOT_MET", "UNIT_MISMATCH",
      "VALUE_DISAGREES_WITH_COUNTS", "VALUE_MISSING", "VALUE_NOT_A_NUMBER", "VALUE_OUT_OF_RANGE",
    ]);
  });

  it("maps every server code to tags, or marks it as left without a tag on purpose", () => {
    for (const code of serverReasonCodes(traceSource())) {
      const entry = REASON_CODE_TAGS[code];
      expect(entry, `${code} is new: map it to a tag in traceReasonTags.ts or mark it "NO_TAG"`).toBeDefined();
      if (entry !== "NO_TAG") for (const id of entry) expect(REASON_TAG_TEXT[id], code).toBeDefined();
    }
    expect(Object.keys(REASON_CODE_TAGS).sort()).toEqual(serverReasonCodes(traceSource())); // no entry for a code the server no longer gives
  });

  it("fails when the server gives a code the table does not know (checked on the source with one code added)", () => {
    const changed = traceSource() + '\nreturn None, f"BRAND_NEW_REASON:{name}"\n';
    const unknown = serverReasonCodes(changed).filter(code => REASON_CODE_TAGS[code] === undefined);
    expect(unknown).toEqual(["BRAND_NEW_REASON"]);
  });

  it("makes each mapped code produce a tag the table declares for it", () => {
    for (const [code, entry] of Object.entries(REASON_CODE_TAGS)) {
      if (entry === "NO_TAG") continue;
      const kind = code === "NO_REQUIRED_CRITERIA" ? "REQUIREMENT" : "CRITERION";
      const state = kind === "REQUIREMENT" ? "HOLD_NO_CRITERIA" : code === "THRESHOLD_NOT_MET" ? "FAIL_COMPUTED" : code === "NO_RESULT" ? "HOLD_NO_RESULT" : code === "NO_RULE" ? "HOLD_NO_RULE" : "HOLD_INVALID_RESULT";
      const made = ids(kind, verdict(kind, state, [code + ":R1"]));
      expect(made.length, code).toBeGreaterThan(0);
      for (const id of made) expect(entry, code).toContain(id);
    }
  });
});
