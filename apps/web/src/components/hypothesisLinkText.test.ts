import { describe, expect, it } from "vitest";
import { linkChangeLine, linkSiblings, noteRequired, RECHECK_REASONS, recheckLine, staleActionNote, staleCount, type HypothesisLink } from "./hypothesisLinkText";

const BANNED = /재시험 필수|원인은|기준을 낮추|통과 가능|승인됨|종결|해결|면제|AI 추천/;
const LEAK = /HOLD_|FAIL_|PASS_|STALE|CONTENT_CHANGED|FLIPPED|SUBJECT_MISSING|NEEDS_RESEARCH|STILL_MATCHES|UNRELATED|verdict|\bnull\b|undefined/;

const link = (patch: Partial<HypothesisLink> = {}): HypothesisLink => ({
  hypothesis_id: "h1", hypothesis_revision_digest: "d".repeat(64), statement: "가설", subject_kind: "CRITERION", subject_id: "C-1", subject_title: "비 조건 탐지율",
  state: "STALE", change: "CONTENT_CHANGED", link_state: "HOLD_NO_RESULT", current_state: "FAIL_COMPUTED", current_verdict_revision: "r".repeat(64), recheck: null, ...patch,
});

describe("what a changed verdict says on a hypothesis", () => {
  it("says the old basis, what it was and what it is now", () => {
    expect(linkChangeLine(link())).toBe("이전 근거 기준 — 판정이 바뀌었습니다(이전: 보류 · 결과 없음 → 지금: 기준 미달)");
  });
  it("says so when met and failed swapped, and when the row is gone", () => {
    const flipped = linkChangeLine(link({ change: "FLIPPED", link_state: "PASS_COMPUTED", current_state: "FAIL_COMPUTED" }));
    expect(flipped).toContain("이전: 기준 충족 → 지금: 기준 미달");
    expect(flipped).toContain("충족과 미달이 뒤바뀌었습니다");
    expect(linkChangeLine(link({ change: "SUBJECT_MISSING", current_state: null }))).toBe("이전 근거 기준 — 이 가설이 나온 추적표 줄이 지금은 없습니다(이전: 보류 · 결과 없음)");
  });
  it("has no line for a current link, none for a link-less hypothesis and a rechecked one is not called old", () => {
    expect(linkChangeLine(link({ state: "CURRENT", change: null }))).toBeNull();
    expect(linkChangeLine(link({ state: "RECHECKED", recheck: { reason_code: "STILL_MATCHES", note: "", actor_id: "human:local-user", created_at: "2026-10-06T00:00:00+00:00", flipped: false } }))).toBeNull();
  });
  it("never shows a code, a forbidden claim or a probability", () => {
    const lines = [link(), link({ change: "FLIPPED", link_state: "PASS_COMPUTED" }), link({ change: "SUBJECT_MISSING", current_state: null }), link({ link_state: "SOMETHING_NEW" })]
      .flatMap(item => [linkChangeLine(item) ?? ""]);
    for (const reason of RECHECK_REASONS) lines.push(reason.label, reason.hint);
    for (const line of lines) { expect(line).not.toMatch(BANNED); expect(line).not.toMatch(LEAK); expect(line).not.toMatch(/\d\s*%|확률/); }
  });
});

describe("the recheck line", () => {
  it("names the reason in words, who and when, and says a research request keeps it old", () => {
    const kept = recheckLine(link({ state: "RECHECKED", recheck: { reason_code: "UNRELATED", note: "", actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00", flipped: false } }));
    expect(kept).toContain("다시 확인됨");
    expect(kept).toContain("판정이 바뀌었지만 이 가설과 관계없음");
    const asked = recheckLine(link({ recheck: { reason_code: "NEEDS_RESEARCH", note: "", actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00", flipped: false } }));
    expect(asked).toContain("다시 조사가 필요함");
    expect(asked).toContain("이전 근거 기준으로 남습니다");
    expect(recheckLine(link())).toBeNull();
  });
});

describe("the words a recheck needs", () => {
  it("asks for words on 'other' and on keeping a link across met/failed, and never on a research request", () => {
    expect(noteRequired("OTHER", false)).toBe(true);
    expect(noteRequired("UNRELATED", false)).toBe(false);
    expect(noteRequired("STILL_MATCHES", true)).toBe(true);
    expect(noteRequired("UNRELATED", true)).toBe(true);
    expect(noteRequired("NEEDS_RESEARCH", true)).toBe(false);
    expect(RECHECK_REASONS.map(reason => reason.code)).toEqual(["UNRELATED", "STILL_MATCHES", "NEEDS_RESEARCH", "OTHER"]);
  });
});

describe("hypotheses of the same change", () => {
  const others = [link({ hypothesis_id: "h2" }), link({ hypothesis_id: "h3", subject_id: "C-2" }), link({ hypothesis_id: "h4", current_verdict_revision: "x".repeat(64) }), link({ hypothesis_id: "h5", state: "CURRENT", change: null })];
  it("picks the old hypotheses of the same row and the same new verdict, and not the current ones", () => {
    expect(linkSiblings(link(), [link(), ...others]).map(item => item.hypothesis_id)).toEqual(["h2"]);
  });
  it("counts old hypotheses per row", () => {
    expect(staleCount([link(), ...others], "CRITERION", "C-1")).toBe(3);
    expect(staleCount([link(), ...others], "CRITERION", "C-2")).toBe(1);
    expect(staleCount([link(), ...others], "REQUIREMENT", "C-1")).toBe(0);
    expect(staleCount(undefined, "CRITERION", "C-1")).toBe(0);
  });
});

describe("a changed basis under the same verdict", () => {
  it("says the verdict state is the same and only the basis changed", () => {
    const line = linkChangeLine(link({ change: "EVIDENCE_ONLY", link_state: "PASS_COMPUTED", current_state: "PASS_COMPUTED" }));
    expect(line).toBe("이전 근거 기준 — 판정 상태는 같고 근거만 바뀌었습니다(판정: 기준 충족)");
    expect(line).not.toMatch(BANNED);
    expect(line).not.toMatch(LEAK);
  });
  it("names an action's stale hypotheses, and the kind of change only when every one of them is a changed basis", () => {
    const stale = link({ hypothesis_id: "h1" });
    const basis = link({ hypothesis_id: "h2", change: "EVIDENCE_ONLY", link_state: "PASS_COMPUTED", current_state: "PASS_COMPUTED" });
    const current = link({ hypothesis_id: "h3", state: "CURRENT", change: null });
    const kept = link({ hypothesis_id: "h4", state: "RECHECKED" });
    expect(staleActionNote(["h3", "h4", "unknown"], [stale, basis, current, kept])).toBeNull();
    expect(staleActionNote(undefined, [stale])).toBeNull();
    expect(staleActionNote(["h1"], undefined)).toBeNull();
    const one = staleActionNote(["h1", "h3"], [stale, current])!;
    expect(one).toContain("이 행동이 기대는 가설 1개가 이전 근거 기준입니다");
    expect(one).not.toContain("근거만 바뀜");
    expect(staleActionNote(["h2"], [basis])).toContain("판정 상태는 같고 근거만 바뀜");
    expect(staleActionNote(["h1", "h2"], [stale, basis])).not.toContain("근거만 바뀜");
    for (const note of [one, staleActionNote(["h2"], [basis])!]) {
      expect(note).toContain("승인 요청과 결정, 실행이 막힙니다");
      expect(note).not.toMatch(BANNED);
      expect(note).not.toMatch(LEAK);
    }
  });
});
