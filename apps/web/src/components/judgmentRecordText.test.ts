import { describe, expect, it } from "vitest";
import type { ClosureEvent, ClosureRow } from "../api/judgmentRecords";
import { CLOSURE_KINDS, CLOSURE_NOTE, closureDisplay, ELIMINATION_NOTE, eliminationLine, eventKindLabel, eventLines, isMet, MATCH_KINDS, previousEvents } from "./judgmentRecordText";

const BANNED = /통과|해결|승인 완료|검증 완료/;
const LEAK = /HOLD_|FAIL_|PASS_|FIX_APPLIED|HUMAN_CLOSED|WAIVER_RECORDED|CONDITION_CHANGED|SINGLE|REPEATED|\bnull\b|undefined/;

const event = (patch: Partial<ClosureEvent> = {}): ClosureEvent => ({
  event_id: "e1", subject_kind: "CRITERION", subject_id: "C-1", kind: "FIX_APPLIED", basis_ref: "ECN-12", note: "", scope: null, verdict_revision: "r".repeat(64),
  verdict_digest: "d", verdict_state: "FAIL_COMPUTED", actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00", ...patch,
});
const row = (patch: Partial<ClosureRow> = {}, last: Partial<ClosureEvent> = {}): ClosureRow => ({
  subject_kind: "CRITERION", subject_id: "C-1", current_state: "FAIL_COMPUTED", effect_confirmed: false, effect_verdict_revision: null, verdict_changed_since: false, events: [event(last)], ...patch,
});

describe("the elimination mark", () => {
  it("says once for one result and repeated for results of two tests, and nothing otherwise", () => {
    expect(eliminationLine("SINGLE")).toBe("배제(결과 1건)");
    expect(eliminationLine("REPEATED")).toBe("배제(반복 확인)");
    expect(eliminationLine(null)).toBeNull();
  });
  it("says it is not a deletion and never gives a probability or a verdict on the cause", () => {
    expect(ELIMINATION_NOTE).toContain("지워지지 않고");
    for (const line of [ELIMINATION_NOTE, ...MATCH_KINDS.map(item => item.label)]) { expect(line).not.toMatch(BANNED); expect(line).not.toMatch(/\d\s*%|확률|원인은/); }
  });
});

describe("the four closures a person can record", () => {
  it("are named as the request names them, and none says passed, solved or approved-and-done", () => {
    expect(CLOSURE_KINDS.map(item => item.label)).toEqual(["수정 반영됨(효과 미확인)", "사람 확인 종결(시험 결과 없음)", "편차·면제 승인(원 기준 미충족 유지)", "운용 조건 변경"]);
    for (const item of CLOSURE_KINDS) { expect(item.label).not.toMatch(BANNED); expect(item.hint).not.toMatch(BANNED); }
    expect(CLOSURE_NOTE).toContain("THOTH가 면제나 승인을 내리지 않으며");
  });
});

describe("what is shown beside a row's verdict", () => {
  it("writes one record as the same lines the top mark uses, so an older record reads the same way", () => {
    const one = event({ note: "배선을 바꿨다" });
    const lines = eventLines("CRITERION", one);
    expect(lines).toEqual(["원래 판정: 기준 미달", expect.stringContaining("기록 · "), "근거 문서: ECN-12", "배선을 바꿨다"]);
    expect(closureDisplay(row({}, { note: "배선을 바꿨다" })).lines.slice(0, 4)).toEqual(lines);
    expect(eventLines("CRITERION", event()).length).toBe(3);
    expect(eventKindLabel(event({ kind: "CONDITION_CHANGED", scope: "50 m 이하 안개" }))).toBe("운용 조건 변경(조건 범위: 50 m 이하 안개)");
    expect(eventKindLabel(event({ kind: "CONDITION_CHANGED", scope: null }))).toBe("운용 조건 변경(조건 범위: 미기재)");
    expect(eventKindLabel(event({ kind: "HUMAN_CLOSED" }))).toBe("사람 확인 종결(시험 결과 없음)");
  });
  it("keeps the older records newest first and none when there is only one", () => {
    expect(previousEvents(row())).toEqual([]);
    const events = [event({ event_id: "e1", basis_ref: "A" }), event({ event_id: "e2", basis_ref: "B" }), event({ event_id: "e3", basis_ref: "C" })];
    expect(previousEvents(row({ events })).map(item => item.event_id)).toEqual(["e2", "e1"]);
  });
  it("shows a recorded fix with the original verdict, who and when, and its document", () => {
    const shown = closureDisplay(row({}, { note: "배선을 바꿨다" }));
    expect(shown.label).toBe("수정 반영됨(효과 미확인)");
    expect(shown.lines).toContain("원래 판정: 기준 미달");
    expect(shown.lines.join("\n")).toContain("근거 문서: ECN-12");
    expect(shown.lines).toContain("배선을 바꿨다");
  });
  it("keeps the original not-met state beside a waiver and says THOTH did not grant it", () => {
    const shown = closureDisplay(row({}, { kind: "WAIVER_RECORDED" }));
    expect(shown.label).toBe("편차·면제 승인(원 기준 미충족 유지)");
    expect(shown.lines).toContain("원래 판정: 기준 미달");
    expect(shown.lines.join("\n")).toContain("THOTH가 면제를 내린 것이 아닙니다");
  });
  it("names the operating condition's range", () => {
    expect(closureDisplay(row({}, { kind: "CONDITION_CHANGED", scope: "50 m 이하 안개" })).label).toBe("운용 조건 변경(조건 범위: 50 m 이하 안개)");
  });
  it("calls the effect confirmed only when the rules did, and says so", () => {
    const shown = closureDisplay(row({ effect_confirmed: true, current_state: "PASS_COMPUTED", verdict_changed_since: true }));
    expect(shown.label).toBe("효과 확인됨");
    expect(shown.lines[0]).toContain("규칙이 계산한 값");
    expect(shown.lines.join("\n")).not.toContain("이 기록 뒤에 판정이 바뀌었습니다");
    expect(closureDisplay(row()).label).not.toBe("효과 확인됨");
  });
  it("says the verdict moved on after the record", () => {
    expect(closureDisplay(row({ verdict_changed_since: true })).lines.join("\n")).toContain("이 기록 뒤에 판정이 바뀌었습니다");
  });
  it("never shows a code or a forbidden claim", () => {
    const rows = [row(), row({}, { kind: "HUMAN_CLOSED" }), row({}, { kind: "WAIVER_RECORDED" }), row({}, { kind: "CONDITION_CHANGED", scope: "x" }), row({ effect_confirmed: true })];
    for (const item of rows) for (const line of [closureDisplay(item).label, ...closureDisplay(item).lines]) { expect(line).not.toMatch(BANNED); expect(line).not.toMatch(LEAK); }
  });
  it("counts only a met verdict as met", () => {
    expect([isMet("PASS_COMPUTED"), isMet("PASS"), isMet("FAIL_COMPUTED"), isMet("HOLD_NO_RESULT"), isMet(null)]).toEqual([true, true, false, false, false]);
  });
});
