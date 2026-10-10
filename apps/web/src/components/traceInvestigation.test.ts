import { describe, expect, it } from "vitest";
import type { TraceView, Verdict } from "../api/trace";
import demo from "./traceDemo.fixture.json";
import { canInvestigate, fillQuestion, investigationFor, threadForRow } from "./traceInvestigation";

const BANNED = /재시험 필수|원인은|기준을 낮추|통과 가능|승인됨|종결|해결|면제|AI 추천/;
// A unit the user wrote (per_min) is theirs to see; the rule's own codes are not.
const LEAK = /HOLD_|FAIL_|PASS_|NO_RESULT|THRESHOLD|VALUE_|UNIT_MISMATCH|\bundefined\b|\bnull\b/;
const phase1 = demo.phase1 as unknown as TraceView;
const phase2 = demo.phase2 as unknown as TraceView;
const verdict = (view: TraceView, kind: string, title: string) => {
  const id = view.items.find(item => item.title === title)!.item_id;
  return view.verdicts.find(item => item.subject_kind === kind && item.subject_id === id) as Verdict;
};

describe("which rows can start an investigation", () => {
  it("offers it for held and failed rows and never for a met one, even a stale one", () => {
    expect(canInvestigate("CRITERION", verdict(phase1, "CRITERION", "Detection rate in rain"))).toBe(true);
    expect(canInvestigate("CRITERION", verdict(phase2, "CRITERION", "Detection rate in rain"))).toBe(true); // failed
    expect(canInvestigate("REQUIREMENT", verdict(phase1, "REQUIREMENT", phase1.items.find(i => i.kind === "REQUIREMENT")!.title))).toBe(true);
    const met = verdict(phase1, "CRITERION", "Detection rate in dry weather");
    expect(canInvestigate("CRITERION", met)).toBe(false);
    expect(canInvestigate("CRITERION", { ...met, currentness: { state: "STALE_BASIS", changed_dependencies: [] } })).toBe(false);
    expect(canInvestigate("CRITERION", { ...met, state: "SOMETHING_NEW" })).toBe(false); // an unknown state gets no button
  });
});

describe("the origin and the question a row gives", () => {
  it("names the row and the revision it was looking at, and nothing the server writes", () => {
    const rain = verdict(phase2, "CRITERION", "Detection rate in rain");
    const found = investigationFor("project:radar", "CRITERION", rain, phase2)!;
    expect(found.origin).toEqual({ kind: "TRACE_VERDICT", project_id: "project:radar", subject_kind: "CRITERION", subject_id: rain.subject_id, verdict_revision: rain.revision_digest });
  });

  it("asks in plain words for a failed row, with the measured value and the bar", () => {
    const found = investigationFor("project:radar", "CRITERION", verdict(phase2, "CRITERION", "Detection rate in rain"), phase2)!;
    expect(found.question).toContain("Detection rate in rain");
    expect(found.question).toMatch(/기준 미달/);
    expect(found.question).toMatch(/측정값 [0-9.]+[이가] 기준 [0-9.]+ 이상/);
    expect(found.question).toContain("원인 후보와 그것을 가를 시험을 찾아 주세요.");
  });

  it("asks what is missing for a held row, in words", () => {
    const found = investigationFor("project:radar", "CRITERION", verdict(phase1, "CRITERION", "Detection rate in fog"), phase1)!;
    expect(found.question).toContain("보류");
    expect(found.question).toContain("날씨: 안개 조건의 결과가 아직 없습니다.");
    expect(found.question).toContain("판정하려면 무엇이 더 필요한지");
  });

  it("covers a requirement row too", () => {
    const title = phase1.items.find(item => item.kind === "REQUIREMENT")!.title;
    const found = investigationFor("project:radar", "REQUIREMENT", verdict(phase1, "REQUIREMENT", title), phase1)!;
    expect(found.origin.subject_kind).toBe("REQUIREMENT");
    expect(found.question).toContain(title);
  });

  it("gives nothing for a met row", () => {
    expect(investigationFor("project:radar", "CRITERION", verdict(phase1, "CRITERION", "Detection rate in dry weather"), phase1)).toBeNull();
  });

  it("never shows an internal code, a forbidden claim or a probability", () => {
    for (const view of [phase1, phase2]) {
      for (const item of view.verdicts) {
        const found = investigationFor("project:radar", item.subject_kind, item, view);
        if (!found) continue;
        expect(found.question).not.toMatch(BANNED);
        expect(found.question).not.toMatch(LEAK);
        expect(found.question).not.toMatch(/\d\s*%|확률/);
        expect(found.question.length).toBeLessThan(2000);
      }
    }
  });
});

describe("finding the thread of a row", () => {
  const thread = (id: string, row: string | null, updated: string, revision = "r") => ({ thread_id: id, project_id: "p", cycle_id: "c", problem: "q", lifecycle: "OPEN",
    execution_state: "IDLE", current_object_ids: [], working_head_digest: "d", updated_at: updated,
    origin: row ? { subject_kind: "CRITERION", subject_id: row, verdict_revision: revision } : null });
  it("picks the same row's thread, the newest one, and none for another row or an ordinary thread", () => {
    const list = [thread("a", "C-1", "2026-10-01"), thread("b", "C-1", "2026-10-03"), thread("c", "C-2", "2026-10-05"), thread("d", null, "2026-10-06")];
    expect(threadForRow(list, "CRITERION", "C-1")?.thread_id).toBe("b");
    expect(threadForRow(list, "CRITERION", "C-2")?.thread_id).toBe("c");
    expect(threadForRow(list, "CRITERION", "C-3")).toBeNull();
    expect(threadForRow(list, "REQUIREMENT", "C-1")).toBeNull(); // kind matters
    expect(threadForRow([{ ...list[3], origin: undefined }], "CRITERION", "C-1")).toBeNull(); // an old thread has no origin
  });
});

describe("filling the question into a draft", () => {
  it("fills an empty draft, keeps a draft that already has it, and puts the question after other text", () => {
    expect(fillQuestion("", "Q")).toBe("Q");
    expect(fillQuestion("  ", "Q")).toBe("Q");
    expect(fillQuestion("Q", "Q")).toBe("Q");
    expect(fillQuestion("내가 쓰던 글", "Q")).toBe("내가 쓰던 글\n\nQ");
  });
});
