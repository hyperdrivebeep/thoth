// @vitest-environment jsdom
import { Tab, Tabs } from "@blueprintjs/core";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { WorkspaceContent } from "./WorkspaceContent";
import { WORKSPACE_TABS_ID, WorkspacePageContext } from "./workspaceTabs";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const show = (page: string | null) =>
  act(async () =>
    root.render(
      <WorkspacePageContext.Provider value={page}>
        <Tabs id={WORKSPACE_TABS_ID} selectedTabId={page ?? undefined}>
          <Tab id="research" title="Conversation" />
          <Tab id="records" title="History" />
        </Tabs>
        <WorkspaceContent className="conversation-page">content</WorkspaceContent>
      </WorkspacePageContext.Provider>,
    ),
  );

it("is the panel that the selected tab controls, named by that tab", async () => {
  await show("records");
  const selected = container.querySelector('[role="tab"][aria-selected="true"]')!;
  const panel = container.querySelector("section")!;
  expect(selected.textContent).toBe("History");
  expect(panel.getAttribute("role")).toBe("tabpanel");
  // The tab comes from Blueprint and the panel from this component: the two ids have to agree.
  expect(selected.getAttribute("aria-controls")).toBe(panel.id);
  expect(panel.getAttribute("aria-labelledby")).toBe(selected.id);
  expect(panel.className).toBe("workspace-content conversation-page");
});

it("follows the tab when another page is shown", async () => {
  await show("records");
  await show("research");
  const selected = container.querySelector('[role="tab"][aria-selected="true"]')!;
  expect(selected.textContent).toBe("Conversation");
  expect(document.getElementById(selected.getAttribute("aria-controls") ?? "")).toBe(
    container.querySelector("section"),
  );
});

it("is plain content where there are no workspace tabs", async () => {
  await act(async () => root.render(<WorkspaceContent>content</WorkspaceContent>));
  const section = container.querySelector("section")!;
  expect(section.getAttribute("role")).toBeNull();
  expect(section.id).toBe("");
  expect(section.className).toBe("workspace-content");
});
