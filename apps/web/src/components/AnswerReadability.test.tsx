import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResearchResultCard } from "./ResearchResultCard";
import { criterionText, plainReason, splitReadable } from "./plainWording";

const digest = "a".repeat(64);
const row = (target: string, question: string, extra: Record<string, unknown> = {}) => ({
  requirement_id: "r-" + target, target, question, status: "UNRESOLVED" as const, applicability: "APPLICABLE",
  relation: "INSUFFICIENT", validation: "INCONCLUSIVE", blocker: "answer:HOLD", review_refs: [], evidence_refs: [], reason_codes: ["answer:HOLD"], ...extra });
const coverage = (rows: ReturnType<typeof row>[]) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, availability: "AVAILABLE" as const,
  reason_codes: [], requirement_set_revision_digest: null, coverage_revision_digest: null, rows,
  summary: { satisfied: 0, unresolved: rows.length, not_applicable: 0, not_assessed: 0, hold_targets: [], reason_codes: [] } });
const progress = (remaining: string[], unknowns: string[] = []) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, result_revision_digest: digest,
  state: "HOLD" as const, currentness: { state: "CURRENT" as const, reasons: [], execution_eligible: true }, progress_items: [], recorded_checks: [],
  remaining_gaps: remaining, unknowns, next_user_action: { schema_version: "1.0.0" as const, action_type: "REVIEW_GAPS" as const, label: "review", reason_codes: [], requires_permission: false, target: null, basis: {} } });
const visible = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "\u0027");
const render = (result: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={result} onDetail={() => {}} {...extra}/>);

const REASONS = [
  "criteria exist but official evaluator inputs are incomplete", "no cutoff-eligible evidence exists",
  "comparison conditions have not been confirmed", "counterevidence has not been checked",
  "measurement implementation is not verified", "criterion semantics still require expert confirmation",
];

describe("the sentences the program writes about missing checks", () => {
  it("has a plain Korean sentence for every recorded sufficiency reason", () => {
    for (const reason of REASONS) {
      const text = plainReason(reason);
      expect(text, reason).not.toBeNull();
      expect(text).toMatch(/[가-힣]/);
      expect(text).not.toMatch(/[A-Za-z]{3,}/);
    }
  });

  it("keeps Korean text, and sets an unknown English sentence aside as technical", () => {
    expect(splitReadable(["외부 검증 필요", REASONS[0], "some new english reason", "42"])).toEqual({
      plain: ["외부 검증 필요", plainReason(REASONS[0]), "42"], technical: ["some new english reason"] });
  });

  it("words the built-in criterion targets and keeps ids out of the title", () => {
    const known = criterionText("answer", "Requested target, fields, source locator and adjacent context");
    expect(known.title).toMatch(/[가-힣]/);
    expect(known.question).toMatch(/[가-힣]/);
    expect(known.question).not.toMatch(/[A-Za-z]{3,}/);
    const id = criterionText("SYN-TC-RAIN", "연결 자료에 비 결과가 있는가?");
    expect(id.title).toBeNull();
    expect(id.question).toBe("연결 자료에 비 결과가 있는가?");
    expect(id.technical).toContain("SYN-TC-RAIN");
    const english = criterionText("something_new", "An English question nobody translated");
    expect(english.question).toBeNull();
    expect(english.technical).toEqual(["something_new", "An English question nobody translated"]);
  });
});

describe("the answer screen", () => {
  it("shows no internal wording outside the folded technical information", () => {
    const html = render({ answer: "a", answer_status: "PARTIAL_HOLD", assessment: { missing_items: REASONS } }, {
      progressSummary: progress([REASONS[1]], [REASONS[2]]),
      coverageMatrix: coverage([
        row("answer", "Requested target, fields, source locator and adjacent context"),
        row("time", "Requested time and source applicability"),
        row("SYN-TC-RAIN", "연결 자료에 비 결과가 있는가?"),
      ]),
    });
    const text = visible(html);
    for (const english of [...REASONS, "Requested target", "Requested time", "INSUFFICIENT", "SYN-TC-RAIN", "answer", "gap"]) {
      expect(text, english).not.toContain(english);
    }
    expect(text).toContain("남은 확인 사항");
    expect(text).toContain("근거 부족");
    expect(text).toContain("연결 자료에 비 결과가 있는가?");
    for (const english of ["SYN-TC-RAIN", REASONS[0]]) expect(html, english).toContain(english); // kept, but folded
  });
});
