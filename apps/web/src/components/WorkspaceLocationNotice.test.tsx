// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import { WorkspaceLocationNotice } from "./WorkspaceLocationNotice";
import { setLocale } from "../i18n/locale";
import type { WorkspaceLocationInfo } from "../api/research";

afterEach(() => setLocale("ko"));
const removable: WorkspaceLocationInfo = { classification: "REMOVABLE", reason_codes: ["REMOVABLE_DRIVE"], confirmed: false,
  message_ko: "이동식 드라이브의 작업 공간입니다. 연결을 유지하고 사용을 마친 뒤 안전하게 제거하세요.",
  message_en: "This workspace is on a removable drive. Keep it connected and remove it safely when done." };

it("keeps a removable warning visible and follows the display language", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); const root = createRoot(container);
  await act(async () => root.render(<WorkspaceLocationNotice location={removable} />));
  expect(container.querySelector('[role="status"]')?.textContent).toBe(removable.message_ko);
  await act(async () => setLocale("en"));
  expect(container.textContent).toBe(removable.message_en);
  await act(async () => root.unmount());
});

it.each(["KNOWN_LOCAL", "UNKNOWN"] as const)("does not invent a warning or confirmation for %s", async classification => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); const root = createRoot(container);
  await act(async () => root.render(<WorkspaceLocationNotice location={{...removable, classification}} />));
  expect(container.textContent).toBe("");
  await act(async () => root.unmount());
});

it("shows the confirmed unknown warning without a fresh confirmation action", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); const root = createRoot(container);
  const location: WorkspaceLocationInfo = {...removable, classification: "UNKNOWN", confirmed: true,
    message_ko: "이 폴더가 동기화·네트워크 폴더가 아닌지 확인하지 못했습니다 (PROBE_FAILED). THOTH는 이 위치의 안전을 보증하지 않습니다.",
    message_en: "THOTH could not confirm that this folder is not a synced or network folder (PROBE_FAILED). It cannot vouch for it."};
  await act(async () => root.render(<WorkspaceLocationNotice location={location} />));
  expect(container.textContent).toBe(location.message_ko);
  expect(container.textContent).not.toContain("accept-location");
  await act(async () => root.unmount());
});
