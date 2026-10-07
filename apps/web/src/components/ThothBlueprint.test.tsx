// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import { Dialog } from "@blueprintjs/core";
import { ThothBlueprint } from "./ThothBlueprint";
import { TraceConfirmDialog } from "./TraceConfirmDialog";

let root: Root | undefined; let container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; });
const mount = async (node: React.ReactNode) => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root!.render(node));
  await act(async () => { await new Promise(r => setTimeout(r, 20)); });
};

it("draws an open dialog in the dark theme", async () => {
  await mount(<ThothBlueprint><Dialog isOpen title="시험"><p>내용</p></Dialog></ThothBlueprint>);
  const portal = document.body.querySelector(".bp6-portal");
  expect(portal?.classList.contains("bp6-dark")).toBe(true);
  expect(portal?.textContent).toContain("내용");
});

it("also covers the dialogs of this app, such as the trace confirmation", async () => {
  const verdict = { subject_kind: "REQUIREMENT", subject_id: "R", state: "PASS" } as never;
  await mount(<ThothBlueprint><TraceConfirmDialog verdict={verdict} title="요구사항" pending={false} error={null} onClose={() => undefined} onSubmit={() => undefined}/></ThothBlueprint>);
  expect(document.body.querySelector(".bp6-portal.bp6-dark .bp6-dialog")).not.toBeNull();
});
