// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import type { ClosureEvent, ClosureRow } from "../api/judgmentRecords";
import type { Verdict } from "../api/trace";
import { ClosureMark } from "./ClosureMark";

const event = (id: string, patch: Partial<ClosureEvent> = {}): ClosureEvent => ({
  event_id: id, subject_kind: "CRITERION", subject_id: "C-1", kind: "FIX_APPLIED", basis_ref: `문서-${id}`, note: "", scope: null, verdict_revision: "r".repeat(64),
  verdict_digest: "d", verdict_state: "FAIL_COMPUTED", actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00", ...patch,
});
const rowOf = (events: ClosureEvent[], patch: Partial<ClosureRow> = {}): ClosureRow => ({
  subject_kind: "CRITERION", subject_id: "C-1", current_state: "FAIL_COMPUTED", effect_confirmed: false, effect_verdict_revision: null, verdict_changed_since: false, events, ...patch,
});
const verdict = { state: "FAIL_COMPUTED" } as unknown as Verdict;
let root: Root | undefined; let container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); });
async function mount(row: ClosureRow) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root!.render(<ClosureMark row={row} verdict={verdict} label="기준 C-1" onRecord={() => undefined}/>));
}
const openHistory = async () => act(async () => [...container.querySelectorAll("button")].find(item => item.textContent?.includes("이전 처분 기록"))!.click());

it("has no history fold for a single record", async () => {
  await mount(rowOf([event("e1")]));
  expect(container.textContent).toContain("근거 문서: 문서-e1");
  expect(container.textContent).not.toContain("이전 처분 기록");
});

it("folds the two older of three records under the latest one, newest first", async () => {
  await mount(rowOf([
    event("e1", { kind: "HUMAN_CLOSED", note: "첫 메모" }),
    event("e2", { kind: "CONDITION_CHANGED", scope: "50 m 이하 안개" }),
    event("e3", { kind: "FIX_APPLIED" }),
  ]));
  expect(container.textContent).toContain("이전 처분 기록 2건");
  await openHistory();
  const notes = [...container.querySelectorAll(".trace-closure-history .trace-closure")].map(node => node.textContent ?? "");
  expect(notes).toHaveLength(2);
  expect(notes[0]).toContain("운용 조건 변경(조건 범위: 50 m 이하 안개)");
  expect(notes[0]).toContain("근거 문서: 문서-e2");
  expect(notes[1]).toContain("사람 확인 종결(시험 결과 없음)");
  expect(notes[1]).toContain("첫 메모");
  expect(notes[1]).toContain("원래 판정: 기준 미달");
  expect(notes[1]).toContain("기록 · ");
  expect(container.textContent).not.toMatch(/FIX_APPLIED|HUMAN_CLOSED|CONDITION_CHANGED|FAIL_COMPUTED|undefined|\bnull\b/);
});

it("puts the rule's effect-confirmed word on the latest record only", async () => {
  await mount(rowOf([event("e1", { kind: "FIX_APPLIED" }), event("e2", { kind: "FIX_APPLIED" })], { effect_confirmed: true, current_state: "PASS_COMPUTED" }));
  await openHistory();
  const top = container.querySelector(".trace-closure-cell > .trace-closure");
  expect(top?.textContent).toContain("효과 확인됨");
  const history = container.querySelector(".trace-closure-history")!;
  expect(history.textContent).not.toContain("효과 확인됨");
  expect(history.textContent).toContain("수정 반영됨(효과 미확인)");
});
