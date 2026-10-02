import { expect, it } from "vitest";
import { catalogLine, checkedAt, excludedModels, optionNote, optionText, type CatalogStatusRow } from "./modelCatalogPresentation";

const now = new Date(2026, 9, 1, 15, 30);
const at = (day: number, hour: number, minute: number) => new Date(2026, 9, day, hour, minute).toISOString();
const row = (patch: Partial<CatalogStatusRow> = {}): CatalogStatusRow => ({ provider: "codex-oauth", source: "PROVIDER_LIST", status: "ACTIVE", fetched_at: at(1, 14, 2), ...patch });

it("says when the list was checked in plain local time", () => {
  expect(checkedAt(at(1, 14, 2), now)).toBe("오늘 14:02");
  expect(checkedAt(new Date(2026, 8, 30, 9, 5).toISOString(), new Date(2026, 9, 1, 8, 0))).toBe("어제 09:05");
  expect(checkedAt(at(1, 14, 2), new Date(2026, 9, 5, 8, 0))).toBe("10월 1일 14:02");
  expect(checkedAt("not a date", now)).toBe("시각 미확인");
});

it("describes a fresh list, a kept list after a failed refresh, and a fixed list", () => {
  expect(catalogLine(row(), now)).toBe("마지막 확인: 오늘 14:02 · 최신");
  expect(catalogLine(row({ status: "STALE_LAST_GOOD", failure_reason: "CATALOG_UNAVAILABLE" }), now)).toBe("마지막 확인: 오늘 14:02 · 마지막 확인 목록 · 갱신 실패");
  expect(catalogLine(row({ source: "CLI_ALIAS", fetched_at: null }), now)).toBe("계정 확인 전 · 고정 목록");
  expect(catalogLine(row({ status: "UNAVAILABLE", fetched_at: null }), now)).toBe("모델 목록을 아직 확인하지 못했습니다");
  expect(catalogLine(undefined, now)).toBeNull();
});

it("marks refused and unchecked models without hiding them", () => {
  expect(optionNote({ provider: "codex-oauth", model: "m", entitlement: "PROVIDER_LISTED", execution: "REJECTED" })).toBe("실행 거부됨");
  expect(optionNote({ provider: "xai-oauth", model: "grok-4.6", entitlement: "UNVERIFIED" })).toBe("계정 확인 전");
  expect(optionNote({ provider: "codex-oauth", model: "m", entitlement: "PROVIDER_LISTED", execution: "VERIFIED" })).toBeNull();
  expect(optionText({ provider: "claude-code", model: "sonnet", entitlement: "UNVERIFIED", label: "최신 Sonnet(별칭) · Claude Code 2.1.284" }, "Anthropic"))
    .toBe("최신 Sonnet(별칭) · Claude Code 2.1.284 · Anthropic · 계정 확인 전");
  expect(optionText({ provider: "codex-oauth", model: "gpt-5.5", entitlement: "PROVIDER_LISTED" }, "OpenAI")).toBe("gpt-5.5 · OpenAI");
});

it("collects the models THOTH left out of every list", () => {
  expect(excludedModels([row({ excluded: [{ model: "gpt-5.4", reason: "UNSUPPORTED_SLUG" }] }), row({ provider: "x" })]))
    .toEqual([{ model: "gpt-5.4", reason: "UNSUPPORTED_SLUG", provider: "codex-oauth" }]);
  expect(excludedModels(undefined)).toEqual([]);
});
