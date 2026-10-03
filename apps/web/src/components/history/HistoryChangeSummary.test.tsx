// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import type { ChangeSummary, HistoryRow } from "../../api/historyModels";
import { ChangeBrief, ChangeDetail } from "./HistoryChangeSummary";
import { HistoryTimeline } from "./HistoryTimeline";

let root: Root | undefined; let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });
async function mount(element: React.ReactElement) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(element));
}
const summary: ChangeSummary = { lines: [{ label: "가설 문장", before: "지연이 12 ms 늘어난다", after: "지연이 20 ms 늘어난다" }, { label: "뒷받침 근거", before: "3개", after: "2개 (추가 0 · 삭제 1)" }],
  more: 2, flags: ["NUMBER", "CITATION"], text_diff_recommended: true };
const quiet: ChangeSummary = { lines: [{ label: "가설 문장", before: "지연이 늘어난다", after: "지연이 커진다" }], more: 0, flags: [], text_diff_recommended: false };

it("names what changed in one line under a history entry, with the flags in Korean", async () => {
  await mount(<ChangeBrief summary={summary}/>);
  expect(node.textContent).toContain("가설 문장");
  expect(node.textContent).toContain("→");
  expect(node.textContent).toContain("외 3개 변경");
  expect(node.textContent).toContain("숫자 변경"); expect(node.textContent).toContain("인용·근거 변경");
  expect(node.textContent).not.toMatch(/NUMBER|CITATION/);
});

it("opens the before and after text by itself when a number, negation, status, citation, authority or time changed", async () => {
  await mount(<ChangeDetail summary={summary}/>);
  const toggle = node.querySelector("button[aria-expanded]") as HTMLButtonElement;
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(node.textContent).toContain("지연이 12 ms 늘어난다"); expect(node.textContent).toContain("지연이 20 ms 늘어난다");
  expect(node.textContent).toContain("숫자·부정·상태·인용·권위·시점");
});

it("reads a human estimate as one Korean line and keeps unnamed fields as a count with the raw form folded under technical info", async () => {
  const estimate: ChangeSummary = { lines: [{ label: "시간 추정", before: "보통(AI)", after: "김(사람)" }], more: 0, flags: [], text_diff_recommended: false,
    other: 2, technical: [{ label: "generation_details", before: "{...}", after: "{...}" }, { label: "scope_note", before: "a", after: "b" }] };
  await mount(<><ChangeBrief summary={estimate}/><ChangeDetail summary={estimate}/></>);
  expect(node.textContent).toContain("시간 추정: 보통(AI) → 김(사람)");
  expect(node.textContent).toContain("외 1개 변경");
  expect(node.textContent).toContain("그 밖의 항목 2개가 바뀌었습니다");
  const technical = [...node.querySelectorAll("button[aria-expanded]")].find(b => b.textContent?.includes("기술 정보")) as HTMLButtonElement;
  expect(technical.getAttribute("aria-expanded")).toBe("false");
  expect(node.querySelectorAll("button[aria-expanded]").length).toBe(2);
  expect(node.textContent).not.toContain("generation_details"); expect(node.textContent).not.toContain("scope_note");
  await act(async () => technical.click());
  expect(node.textContent).toContain("generation_details"); expect(node.textContent).toContain("scope_note");
});

it("says only the count when every changed field is unnamed", async () => {
  await mount(<ChangeBrief summary={{ lines: [], more: 0, flags: [], text_diff_recommended: false, other: 3, technical: [] }}/>);
  expect(node.textContent).toContain("그 밖의 항목 3개 변경");
});

it("keeps the text collapsed for a plain rewording and shows nothing without a summary", async () => {
  await mount(<ChangeDetail summary={quiet}/>);
  expect((node.querySelector("button[aria-expanded]") as HTMLButtonElement).getAttribute("aria-expanded")).toBe("false");
  await act(async () => root!.unmount()); node.remove();
  await mount(<><ChangeDetail summary={null}/><ChangeBrief summary={undefined}/></>);
  expect(node.textContent).toBe("");
});

it("shows the summary under the entry in the timeline", async () => {
  const row: HistoryRow = { id: "row-1", kind: "REVISION", title: "가설 변경", occurredAt: "2026-09-30T00:00:00Z", association: "UNATTRIBUTED", membership: "CURRENT",
    availability: "AVAILABLE", currentness: { state: "CURRENT", reasons: [] }, changeSummary: summary,
    selection: { kind: "record", scope: { projectId: "p" }, record: { owner: "SEMANTIC_REVISION", projectId: "p", id: "r", digest: "a".repeat(64) } } };
  await mount(<HistoryTimeline pages={[{ items: [row], coverage: { visibility: "AUTHORIZED_SUBSET", association: "EXACT", scan: "COMPLETE_PAGE", reasons: [] }, nextCursor: null, actorScope: "s" } as never]}
    selected={null} loadingMore={false} onSelect={() => undefined} onMore={() => undefined}/>);
  expect(node.querySelector(".history-event")?.textContent).toContain("가설 문장");
  expect(node.querySelector(".history-event")?.textContent).toContain("숫자 변경");
});
