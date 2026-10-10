// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TracePage } from "./TracePage";
import type { RowInvestigation } from "./traceInvestigation";
import demo from "./traceDemo.fixture.json";

type Json = Record<string, unknown>;
const state = vi.hoisted(() => ({ view: {} as Json, calls: [] as { method: string; input: Json }[], preview: {} as Json, applyError: null as string | null,
  spans: [] as Json[], readFails: false, applied: {} as Json, afterConfirm: {} as Json, exported: {} as Json, links: [] as Json[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Json) => {
    state.calls.push({ method, input });
    const done = (value: unknown) => ({ value, state: "SUCCEEDED", operation_id: "" });
    if (method === "trace/read") { if (state.readFails) throw new Error("synthetic read failure"); return done(state.view); }
    if (method === "trace/importPreview") return done(state.preview);
    if (method === "trace/importApply") {
      if (state.applyError) { const { RpcError } = await importOriginal<typeof import("../api/rpcClient")>(); throw new RpcError(state.applyError, -32030, {}); }
      state.view = demo.phase2 as Json; return done(state.applied);
    }
    if (method === "trace/confirm") { state.view = state.afterConfirm; return done(state.view); }
    if (method === "trace/export") return done(state.exported);
    if (method === "trace/history") return done(demo.manyOlder);
    if (method === "evidence/list") return done({ evidence: state.spans });
    if (method === "trace/closure/list") return done({ closures: [] });
    if (method === "hypothesis/link/list") return done({ trace_digest: null, links: state.links, reason_distribution: { total: 0, flipped: 0, by_reason: {} } });
    throw new Error("unexpected " + method);
  } }));

let root: Root | undefined; let container: HTMLDivElement;
const settle = async () => { for (let i = 0; i < 10; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); }); };
async function mount(props: { onInvestigate?: (item: RowInvestigation) => void; focusRow?: { kind: string; id: string } | null } = {}) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><TracePage projectId="project:radar" {...props}/></QueryClientProvider>));
  await settle();
}
beforeEach(() => { state.links = []; state.view = demo.phase1 as Json; state.calls = []; state.applyError = null; state.spans = []; state.readFails = false; state.preview = demo.rainPreview as Json;
  state.applied = demo.rainApply as Json; state.afterConfirm = demo.afterConfirm as Json; state.exported = demo.exported as Json; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; vi.restoreAllMocks(); });

const withoutTech = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "");
const page = () => withoutTech(container.textContent === null ? "" : container.innerHTML);
const visibleText = (scope: ParentNode = document.body) => {
  const copy = (scope as HTMLElement).cloneNode(true) as HTMLElement;
  copy.querySelectorAll("details").forEach(node => node.remove());
  return copy.textContent ?? "";
};
const buttonByLabel = (label: string, scope: ParentNode = document.body) => [...scope.querySelectorAll("button")].find(b => (b.getAttribute("aria-label") ?? b.textContent?.trim()) === label) ?? null;
const click = async (element: Element | null) => { expect(element).not.toBeNull(); await act(async () => (element as HTMLElement).click()); await settle(); };
const rowOf = (text: string) => [...container.querySelectorAll("tr")].find(tr => tr.querySelector("th")?.textContent?.includes(text)) as HTMLElement;
const RAW = ["HOLD_NO_RESULT", "PASS_COMPUTED", "FAIL_COMPUTED", "HOLD_INCOMPLETE", "FAIL_WITH_INCOMPLETE_COVERAGE", "NO_RESULT:", "THRESHOLD_NOT_MET", "CURRENT", "STALE_BASIS", "CSV_IMPORT"];

it("shows the first-phase data with rain and fog held, dry passing, and no internal codes on screen", async () => {
  await mount();
  const text = visibleText(container);
  const rain = rowOf("Detection rate in rain"); const fog = rowOf("Detection rate in fog"); const dry = rowOf("Detection rate in dry weather");
  expect(rain.textContent).toContain("보류 · 결과 없음"); expect(fog.textContent).toContain("보류 · 결과 없음");
  expect(dry.textContent).toContain("기준 충족");
  expect(rain.textContent).toContain("날씨: 비 조건의 결과가 아직 없습니다.");
  expect(rain.textContent).toContain("조건 날씨: 비"); expect(visibleText(container)).not.toMatch(/weather=/);
  expect(rowOf("Synthetic radar detects").textContent).toContain("보류 · 결과 없는 조건이 있음");
  expect(rowOf("Synthetic radar detects").textContent).toContain("기준 6개 · 충족 2 · 미달 0 · 보류 4");
  for (const raw of RAW) expect(text).not.toContain(raw);
  for (const banned of ["엑셀 대체", "인증", "검증 완료"]) expect(document.body.textContent).not.toContain(banned);
  expect(container.querySelector(".trace-synthetic-banner")?.textContent).toContain("합성 데이터입니다");
  expect(container.querySelector(".trace-synthetic-banner")?.textContent).toContain("SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE");
  expect(text).toContain("0.95 ratio (19/20)");
  expect(page()).not.toContain("엑셀 대체");
});

it("does not label SYN-prefixed IDs as synthetic without an explicit notice or marker", async () => {
  const view = { ...structuredClone(demo.phase1), items: demo.phase1.items.map(item => ({ ...item, fields: {} })) };
  expect(view.items.every(item => item.item_id.startsWith("SYN-"))).toBe(true);
  expect(view.items.every(item => !item.title.includes("SYNTHETIC DEMO DATA"))).toBe(true);
  state.view = view as Json;
  await mount();
  expect(rowOf("Detection rate in rain").textContent).toContain("보류 · 결과 없음");
  expect(container.querySelector(".trace-synthetic-banner")).toBeNull();
});

it("is a labelled table whose rows open and close from a named button", async () => {
  await mount();
  expect(container.querySelector("table caption")?.textContent).toContain("판정");
  expect([...container.querySelectorAll("thead th")].every(th => th.getAttribute("scope") === "col")).toBe(true);
  const open = buttonByLabel("Detection rate in rain 판정 이력 펼치기", container) as HTMLButtonElement;
  expect(open.tagName).toBe("BUTTON"); expect(open.getAttribute("aria-expanded")).toBe("false");
  await click(open);
  const close = buttonByLabel("Detection rate in rain 판정 이력 접기", container) as HTMLButtonElement;
  expect(close.getAttribute("aria-expanded")).toBe("true");
  expect(document.getElementById(close.getAttribute("aria-controls")!)?.textContent).toContain("처음 계산한 판정입니다.");
  expect(buttonByLabel("CSV로 내보내기", container)).not.toBeNull();
  expect(buttonByLabel("CSV 들여오기", container)).not.toBeNull();
  expect(buttonByLabel("Detection rate in dry weather 판정 확인 기록 남기기", container)).not.toBeNull();
  expect(buttonByLabel("Detection rate in dry weather 근거 위치 1 보기", container)).not.toBeNull();
});

async function chooseFile(text: string) {
  const input = document.body.querySelector<HTMLInputElement>("input[type=file]")!;
  const file = new File([text], "rain.csv", { type: "text/csv" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  await settle();
}

it("previews the rain results, applies them, and then only the rain lines fail while the old hold stays in the history", async () => {
  await mount();
  const dryBefore = rowOf("Detection rate in dry weather").textContent;
  await click(buttonByLabel("CSV 들여오기", container));
  expect(document.body.querySelector<HTMLInputElement>("input[value=UPDATE]")?.checked).toBe(true);
  await chooseFile(demo.rainCsv);
  const preview = state.calls.find(call => call.method === "trace/importPreview")!;
  expect(preview.input).toMatchObject({ project_id: "project:radar", mode: "UPDATE", csv_text: demo.rainCsv.replace(/^\uFEFF/, "") });
  const dialog = document.body.querySelector(".trace-import-dialog")!;
  expect(visibleText(dialog)).toContain("추가 6개 · 변경 0개 · 삭제 0개");
  expect(visibleText(dialog)).toContain("아직 저장하지 않았습니다");
  expect(state.calls.some(call => call.method === "trace/importApply")).toBe(false);
  await click(buttonByLabel("이 내용으로 반영", dialog));
  const apply = state.calls.find(call => call.method === "trace/importApply")!;
  expect(apply.input).toMatchObject({ mode: "UPDATE", preview_id: demo.rainPreview.preview_id, input_sha256: demo.rainPreview.input_sha256 });
  expect(rowOf("Detection rate in rain").textContent).toContain("기준 미달");
  expect(rowOf("False tracks per minute in rain").textContent).toContain("기준 미달");
  expect(rowOf("Synthetic radar detects").textContent).toContain("기준 미달 · 결과 없는 조건도 있음");
  expect(rowOf("Detection rate in dry weather").textContent).toBe(dryBefore);
  expect(rowOf("Detection rate in fog").textContent).toContain("보류 · 결과 없음");
  expect(rowOf("Detection rate in rain").textContent).toContain("측정값 0.80이 기준 0.90 이상(ratio)에 못 미칩니다.");
  const notice = container.querySelector(".trace-notice")!.textContent!;
  expect(notice).toContain("반영했습니다."); expect(notice).toContain("보류 · 결과 없음 → 기준 미달");
  await click(buttonByLabel("Detection rate in rain 판정 이력 펼치기", container));
  const history = visibleText(container.querySelector("[aria-label='판정 이력']")!);
  expect(history).toContain("현재 판정"); expect(history).toContain("이전 판정");
  expect(history).toContain('이전 판정 "보류 · 결과 없음" → 이번 판정 "기준 미달"');
  expect(history).toContain("CSV 들여오기 반영"); expect(history).toContain("결과 추가: SYN-RES-SYN-C-DET-RAIN (0.80 ratio (16/20) · 날씨: 비)");
  expect(history).toContain("날씨: 비 조건의 결과가 아직 없습니다."); expect(history).not.toMatch(/weather=/);
  expect(container.querySelector(".trace-revision:last-child details")?.textContent).toContain("NO_RESULT:weather=rain");
  expect(history).toContain("SYN-RES-SYN-C-DET-RAIN (개정 1): 0.80 ratio (16/20)");
  for (const raw of RAW) expect(history).not.toContain(raw);
  expect(container.querySelector(".trace-revision details")?.textContent).toContain("판정 개정");
});

it("explains a refused file in words with the next step and keeps the apply button off", async () => {
  state.view = demo.phase2 as Json; state.preview = demo.staleBasePreview as Json;
  await mount();
  await click(buttonByLabel("CSV 들여오기", container));
  await chooseFile("stale");
  const dialog = document.body.querySelector(".trace-import-dialog")!;
  const text = visibleText(dialog);
  expect(text).toContain("지금의 추적표와 다른 상태에서 내보낸 것입니다");
  expect(text).toContain("다음 행동: 지금 상태에서 'CSV로 내보내기'를 다시 한 뒤");
  expect(text).not.toContain("STALE_BASE");
  expect(dialog.querySelector("details")?.textContent).toContain("STALE_BASE");
  expect((buttonByLabel("이 내용으로 반영", dialog) as HTMLButtonElement).disabled).toBe(true);
  expect(state.calls.some(call => call.method === "trace/importApply")).toBe(false);
});

it("says the preview went stale when the server refuses the apply and changes nothing", async () => {
  state.applyError = "TRACE_IMPORT_PREVIEW_STALE";
  await mount();
  await click(buttonByLabel("CSV 들여오기", container));
  await chooseFile(demo.rainCsv);
  await click(buttonByLabel("이 내용으로 반영", document.body.querySelector(".trace-import-dialog")!));
  expect(visibleText(document.body.querySelector(".trace-import-dialog")!)).toContain("미리보기를 본 뒤에 추적표가 바뀌어 반영하지 않았습니다");
  expect(rowOf("Detection rate in rain").textContent).toContain("보류 · 결과 없음");
});

it("records a confirmation with a reason and leaves every verdict as it was", async () => {
  await mount();
  const before = rowOf("Synthetic radar detects").textContent!.split("확인")[0];
  await click(container.querySelector(".trace-requirement button[aria-label$='판정 확인 기록 남기기']"));
  const dialog = document.body.querySelector(".bp6-dialog")!;
  expect(visibleText(dialog)).toContain("판정은 바뀌지 않고");
  await click(buttonByLabel("확인 기록 남기기", dialog));
  expect(visibleText(dialog)).toContain("확인한 이유를 적어 주세요.");
  expect(state.calls.some(call => call.method === "trace/confirm")).toBe(false);
  const area = dialog.querySelector<HTMLTextAreaElement>("textarea[aria-label='확인한 이유']")!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(area, "1단계 결과 확인"); area.dispatchEvent(new Event("input", { bubbles: true })); });
  await click(buttonByLabel("확인 기록 남기기", dialog));
  const sent = state.calls.find(call => call.method === "trace/confirm")!;
  const requirement = (demo.phase1.verdicts as { subject_kind: string; revision_digest: string }[]).find(item => item.subject_kind === "REQUIREMENT")!;
  expect(sent.input).toMatchObject({ project_id: "project:radar", verdict_revision_digest: requirement.revision_digest, rationale: "1단계 결과 확인", expected_digest: demo.phase1.record_digest });
  const after = rowOf("Synthetic radar detects").textContent!;
  expect(after).toContain("이 컴퓨터의 사용자 확인"); expect(after).toContain("1단계 결과 확인");
  expect(after).toContain("보류 · 결과 없는 조건이 있음"); expect(after.startsWith(before.slice(0, 10))).toBe(true);
  expect(rowOf("Detection rate in rain").textContent).toContain("보류 · 결과 없음");
  expect(container.querySelector(".trace-notice")?.textContent).toContain("판정은 바뀌지 않았습니다");
});

it("opens a 근거 위치 on the sentence it points at, or says why it cannot", async () => {
  await mount();
  const ref = "30_RESULT_DRY_SYNTHETIC.yaml#detection";
  await click(buttonByLabel("Detection rate in dry weather 근거 위치 1 보기", container));
  let dialog = document.body.querySelector(".trace-evidence-dialog")!;
  expect(visibleText(dialog)).toContain("이 위치를 이 프로젝트에 연결된 자료에서 찾지 못했습니다");
  expect(visibleText(dialog)).toContain("연결되어 있다는 것만으로 THOTH가 그 문장을 근거로 확인한 것은 아닙니다");
  expect(dialog.textContent).toContain(ref);
  await click(buttonByLabel("닫기", dialog));
  state.spans = [{ span_id: ref, artifact_id: "artifact:1", source_version_id: "v1", exact_text: "맑은 날 20회 중 19회 탐지했다", authority_state: "OFFICIAL", cutoff_state: "VALID",
    verification_state: "UNVERIFIED", support_state: "SUPPORTED", locator: { line: 12 } },
    { span_id: "span:other", artifact_id: "artifact:1", source_version_id: "v1", exact_text: "앞 문장", authority_state: "OFFICIAL", cutoff_state: "VALID", verification_state: "UNVERIFIED", support_state: "SUPPORTED", locator: { line: 11 } }];
  document.body.innerHTML = ""; if (root) await act(async () => root!.unmount()); await mount();
  const scrolled = vi.fn(); Object.assign(Element.prototype, { scrollIntoView: scrolled });
  await click(buttonByLabel("Detection rate in dry weather 근거 위치 1 보기", container));
  dialog = document.body.querySelector(".trace-evidence-dialog")!;
  const focused = dialog.querySelector("[aria-current=true]")!;
  expect(focused.textContent).toContain("맑은 날 20회 중 19회 탐지했다"); expect(focused.getAttribute("data-span-id")).toBe(ref);
  expect(dialog.querySelectorAll("[data-span-id]").length).toBe(2);
  // Opening the window scrolls the highlighted sentence into view inside the window.
  expect(scrolled).toHaveBeenCalledWith(expect.objectContaining({ block: "center" }));
  expect(scrolled.mock.contexts).toContain(focused);
  // The sentences read in the order of the file, so the highlighted one sits where it is in the document.
  expect([...dialog.querySelectorAll("[data-span-id]")].map(card => card.getAttribute("data-span-id"))).toEqual(["span:other", ref]);
});

it("downloads the CSV and says what the file cannot carry", async () => {
  const create = vi.fn(() => "blob:x"); Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
  const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  await mount();
  await click(buttonByLabel("CSV로 내보내기", container));
  expect(state.calls.some(call => call.method === "trace/export")).toBe(true);
  expect(create).toHaveBeenCalledTimes(1); expect(clicked).toHaveBeenCalledTimes(1);
  const blob = (create.mock.calls[0] as unknown as [Blob])[0];
  expect(await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob); })).toBe((demo.exported as { csv_text: string }).csv_text.replace(/^\uFEFF/, ""));
  const note = container.querySelector(".trace-notice")!.textContent!;
  expect(note).toContain("판정 이력(들여온 뒤 다시 계산됩니다)"); expect(note).not.toContain("verdict_history");
});

it("marks a line whose basis changed as needing a new calculation and names the change", async () => {
  const view = structuredClone(demo.phase2) as { verdicts: Json[] };
  const rain = view.verdicts.find(item => item.subject_id === "SYN-C-DET-RAIN")!;
  rain.currentness = { state: "STALE_BASIS", changed_dependencies: [{ kind: "RESULT", ref_id: "SYN-RES-X", field: "value", before: "0.80", after: "0.92", before_revision: 1, after_revision: 2, criterion_ids: ["SYN-C-DET-RAIN"], requirement_ids: [] }] };
  state.view = view as Json;
  await mount();
  const text = rowOf("Detection rate in rain").textContent!;
  expect(text).toContain("근거가 바뀜 · 다시 계산 필요"); expect(text).toContain("결과 SYN-RES-X의 측정값: 0.80 → 0.92");
  expect(rowOf("Detection rate in dry weather").textContent).toContain("지금 입력 기준");
});

it("starts an empty project in create mode and says there is no table yet, and a failed read is not shown as empty", async () => {
  state.view = demo.empty as Json;
  await mount();
  expect(container.textContent).toContain("아직 추적표가 없습니다");
  expect(container.querySelector(".trace-synthetic-banner")).toBeNull();
  await click(buttonByLabel("CSV 들여오기", container));
  expect(document.body.querySelector<HTMLInputElement>("input[value=CREATE]")?.checked).toBe(true);
  document.body.innerHTML = ""; if (root) await act(async () => root!.unmount()); root = undefined;
  state.readFails = true; await mount();
  expect(container.textContent).toContain("추적표를 읽지 못했습니다. 비어 있다는 뜻이 아닙니다.");
  expect(container.textContent).not.toContain("아직 추적표가 없습니다");
});

it("shows the three latest revisions of a long history and reads older ones only when asked", async () => {
  state.view = demo.many as Json;
  await mount();
  await click(buttonByLabel("Detection rate in rain 판정 이력 펼치기", container));
  const region = () => container.querySelector("[aria-label='판정 이력']")!;
  expect(region().querySelectorAll(".trace-revision")).toHaveLength(3);
  expect(visibleText(container)).toContain("판정 이력 전체 6개 중 3개를 보고 있습니다.");
  expect(visibleText(region())).toContain('이 앞의 판정은 "이전 이력 더 보기"로 볼 수 있습니다.');
  expect(state.calls.some(call => call.method === "trace/history")).toBe(false);
  const rain = (demo.many.verdicts as { subject_id: string; recent_history: { revision_digest: string }[] }[]).find(item => item.subject_id === "SYN-C-DET-RAIN")!;
  await click(buttonByLabel("이전 이력 더 보기", container));
  const asked = state.calls.find(call => call.method === "trace/history")!;
  expect(asked.input).toMatchObject({ project_id: "project:radar", subject_kind: "CRITERION", subject_id: "SYN-C-DET-RAIN", before_digest: rain.recent_history[2].revision_digest });
  expect(region().querySelectorAll(".trace-revision")).toHaveLength(6);
  expect(visibleText(container)).toContain("판정 이력 전체 6개 중 6개를 보고 있습니다.");
  expect(buttonByLabel("이전 이력 더 보기", container)).toBeNull();
  expect(visibleText(region())).toContain("처음 계산한 판정입니다.");
  expect(visibleText(region())).not.toContain('이 앞의 판정은 "이전 이력 더 보기"로 볼 수 있습니다.');
});

const NEXT_BANNED = ["재시험 필수", "원인은", "기준을 낮추", "통과 가능", "승인됨", "종결", "해결", "면제", "AI 추천"];
const tags = (row: HTMLElement) => [...row.querySelectorAll(".bp6-tag")].filter(tag => /bp6-intent-/.test(tag.className)).map(tag => tag.textContent + "|" + tag.className.match(/bp6-intent-\w+/)?.[0]);

it("shows a next check under the held lines, with who decides, and none under the met line", async () => {
  await mount();
  for (const title of ["Detection rate in rain", "Detection rate in fog"]) {
    const next = rowOf(title).querySelector(".trace-next")!;
    expect(next.getAttribute("role")).toBe("note");
    expect(next.textContent).toContain("다음 확인");
    expect(next.textContent).toContain("연결된 시험의 결과가 아직 들어오지 않았습니다"); // the 미시험 tag's own check
    expect(next.textContent).toContain("결정: 시험 책임자");
  }
  expect(rowOf("Detection rate in dry weather").querySelector(".trace-next")).toBeNull();
  const requirement = rowOf("Synthetic radar detects").querySelector(".trace-next")!;
  expect(requirement.textContent).toContain("각 기준 줄의 '다음 확인'을 따릅니다");
  expect(requirement.textContent).not.toContain("결정:");
  const text = visibleText(container);
  for (const banned of NEXT_BANNED) expect(text).not.toContain(banned);
  for (const raw of RAW) expect(text).not.toContain(raw);
});

it("after the rain results, the failed lines say what to check and the requirement says both things, without changing a verdict", async () => {
  await mount();
  const before = { dry: tags(rowOf("Detection rate in dry weather")), fog: tags(rowOf("Detection rate in fog")), rainTags: tags(rowOf("Detection rate in rain")) };
  expect(before.rainTags[0]).toContain("보류 · 결과 없음");
  state.view = demo.phase2 as Json;
  document.body.innerHTML = ""; if (root) await act(async () => root!.unmount()); await mount();
  const rain = rowOf("Detection rate in rain");
  expect(tags(rain)[0]).toContain("기준 미달"); expect(tags(rain)[0]).toContain("bp6-intent-danger");
  expect(rain.querySelector(".trace-next")!.textContent).toContain("시험이 유효했는지, 다른 원인 후보가 있는지");
  expect(rain.querySelector(".trace-next")!.textContent).toContain("결정: 해당 권한자");
  const requirement = rowOf("Synthetic radar detects").querySelector(".trace-next")!;
  const lines = [...requirement.querySelectorAll("li")].map(item => item.textContent!);
  expect(lines).toHaveLength(2);
  expect(lines[0]).toContain("원인 후보"); expect(lines[1]).toContain("시험하지 않은 조건"); expect(lines[1]).toContain("시험 계획");
  expect(lines[0]).toContain("결정: 해당 권한자"); expect(lines[1]).toContain("결정: 시험 책임자"); // each line names its own decider
  expect(tags(rowOf("Detection rate in dry weather"))).toEqual(before.dry);
  expect(tags(rowOf("Detection rate in fog"))).toEqual(before.fog);
  expect(rowOf("Detection rate in dry weather").querySelector(".trace-next")).toBeNull();
  const text = visibleText(container);
  for (const banned of NEXT_BANNED) expect(text).not.toContain(banned);
  for (const raw of RAW) expect(text).not.toContain(raw);
});

it("on a line whose basis changed, says the old guidance is for the old basis and keeps the verdict", async () => {
  const view = structuredClone(demo.phase2) as { verdicts: Json[] };
  const rain = view.verdicts.find(item => item.subject_id === "SYN-C-DET-RAIN")!;
  rain.currentness = { state: "STALE_BASIS", changed_dependencies: [{ kind: "RESULT", ref_id: "SYN-RES-X", field: "value", before: "0.80", after: "0.92", before_revision: 1, after_revision: 2, criterion_ids: ["SYN-C-DET-RAIN"], requirement_ids: [] }] };
  state.view = view as Json;
  await mount();
  const row = rowOf("Detection rate in rain");
  expect(tags(row)).toContain("기준 미달|bp6-intent-danger");
  const next = row.querySelector(".trace-next")!.textContent!;
  expect(next).toContain("바뀐 근거로 판정을 다시 볼지"); expect(next).toContain("이전 안내는 이전 근거 기준입니다");
  expect(next).toContain("시험이 유효했는지");
});

it("shows the tags that say why a line is held or failed, in grey with a one-line hint, and none on a met line", async () => {
  await mount();
  const tagTexts = (row: HTMLElement) => [...row.querySelectorAll(".trace-tags .bp6-tag")].map(tag => tag.textContent);
  expect(tagTexts(rowOf("Detection rate in rain"))).toEqual(["미시험"]);
  expect(tagTexts(rowOf("Detection rate in fog"))).toEqual(["미시험"]);
  expect(tagTexts(rowOf("Synthetic radar detects"))).toEqual(["필수 기준 미완료"]);
  expect(rowOf("Detection rate in dry weather").querySelector(".trace-tags")).toBeNull();
  const tag = rowOf("Detection rate in rain").querySelector(".trace-tags .bp6-tag") as HTMLElement;
  expect(tag.getAttribute("title")).toContain("연결된 시험은 있지만 아직 결과가 없습니다");
  expect(tag.className).not.toMatch(/bp6-intent-/);
  state.view = demo.phase2 as Json;
  document.body.innerHTML = ""; if (root) await act(async () => root!.unmount()); await mount();
  expect(tagTexts(rowOf("Detection rate in rain"))).toEqual(["유효한 미달"]);
  expect(tagTexts(rowOf("Detection rate in fog"))).toEqual(["미시험"]);
  expect(tagTexts(rowOf("Synthetic radar detects"))).toEqual(["필수 기준 미완료"]);
  expect(tagTexts(rowOf("Detection rate in dry weather"))).toEqual([]);
  const text = visibleText(container);
  for (const raw of RAW) expect(text).not.toContain(raw);
});

it("offers the cause investigation only on held and failed rows, and pressing it hands the row to the workspace without starting anything", async () => {
  const asked: RowInvestigation[] = [];
  await mount({ onInvestigate: item => asked.push(item) });
  const names = () => [...container.querySelectorAll(".trace-investigate")].map(button => button.getAttribute("aria-label"));
  expect(names()).toContain("Detection rate in rain 원인 조사");
  expect(names()).toContain("Detection rate in fog 원인 조사");
  expect(names().some(name => name?.startsWith("Synthetic radar detects"))).toBe(true); // the held requirement
  expect(names()).not.toContain("Detection rate in dry weather 원인 조사"); // a met row has none
  expect(rowOf("Detection rate in dry weather").querySelector(".trace-investigate")).toBeNull();
  const before = state.calls.length;
  await click(buttonByLabel("Detection rate in rain 원인 조사", container));
  expect(asked).toHaveLength(1);
  expect(asked[0].origin).toMatchObject({ kind: "TRACE_VERDICT", project_id: "project:radar", subject_kind: "CRITERION" });
  expect(asked[0].question).toContain("Detection rate in rain");
  expect(asked[0].question).not.toMatch(/HOLD_|FAIL_|PASS_|NO_RESULT/);
  expect(state.calls.length).toBe(before); // no request went to the server; the user sends the question
  expect(state.calls.every(call => ["trace/read", "hypothesis/link/list", "trace/closure/list"].includes(call.method))).toBe(true); // only reads
});

it("has no investigation button when the page is not given a place to open one, and marks the row a result came from", async () => {
  await mount();
  expect(container.querySelector(".trace-investigate")).toBeNull();
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  await mount({ onInvestigate: () => undefined, focusRow: { kind: "CRITERION", id: "SYN-C-DET-FOG" } });
  const marked = [...container.querySelectorAll(".trace-focus")];
  expect(marked).toHaveLength(1);
  expect(marked[0].getAttribute("data-trace-row")).toBe("CRITERION:SYN-C-DET-FOG");
});


it("tells, beside the investigation link, how many hypotheses of that row stand on an old verdict", async () => {
  const rain = demo.phase1.items.find(item => item.title === "Detection rate in rain")!.item_id;
  const stale = (id: string, state = "STALE") => ({ hypothesis_id: id, subject_kind: "CRITERION", subject_id: rain, state });
  state.links = [stale("h1"), stale("h2"), stale("h3", "CURRENT"), stale("h4", "RECHECKED")];
  await mount({ onInvestigate: () => undefined });
  const tag = (row: HTMLElement) => [...row.querySelectorAll(".bp6-tag")].map(item => item.textContent).filter(text => text?.includes("낡은 가설"));
  expect(tag(rowOf("Detection rate in rain"))).toEqual(["낡은 가설 2개"]);
  expect(tag(rowOf("Detection rate in fog"))).toEqual([]);
  expect(visibleText(container)).not.toMatch(/STALE|HOLD_|CRITERION/);
});

it("does not ask for the hypothesis marks when the page cannot open a conversation, and survives a failed read", async () => {
  await mount();
  expect(state.calls.some(call => call.method === "hypothesis/link/list")).toBe(false);
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  state.links = [{ hypothesis_id: "h", subject_kind: "CRITERION", subject_id: "x", state: "STALE" }];
  await mount({ onInvestigate: () => undefined });
  expect(state.calls.some(call => call.method === "hypothesis/link/list")).toBe(true);
  expect(container.querySelectorAll(".trace-investigate").length).toBeGreaterThan(0);
});
