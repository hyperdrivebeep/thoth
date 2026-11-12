// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { forgetProject } from "./forgetProject";
import {
  inspectLocalContext,
  readLocalDraft,
  writeLocalContext,
  writeLocalDraft,
} from "./localWorkspacePersistence";

const HERE = `workspace:${"c".repeat(32)}`;
const OTHER = `workspace:${"d".repeat(32)}`;
afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

it("removes drafts and the last place of the deleted project in this workspace and nothing else", () => {
  writeLocalDraft(HERE, "gone", "t1", "unsent one");
  writeLocalDraft(HERE, "gone", "t2", "unsent two");
  writeLocalDraft(HERE, "kept", "t1", "other project");
  writeLocalDraft(OTHER, "gone", "t1", "same id, other workspace");
  writeLocalContext(HERE, { projectId: "gone", threadId: "t1" });
  sessionStorage.setItem(`thoth:draft:v1:${JSON.stringify(["gone", "t1"])}`, "x");
  sessionStorage.setItem(`thoth:draft:v1:${JSON.stringify(["kept", "t1"])}`, "y");
  forgetProject({ mode: "LOCAL", workspaceId: HERE }, "gone");
  expect(readLocalDraft(HERE, "gone", "t1")).toBe("");
  expect(readLocalDraft(HERE, "gone", "t2")).toBe("");
  expect(readLocalDraft(HERE, "kept", "t1")).toBe("other project");
  expect(readLocalDraft(OTHER, "gone", "t1")).toBe("same id, other workspace");
  expect(inspectLocalContext(HERE).context).toEqual({ projectId: "", threadId: "" });
  expect(sessionStorage.length).toBe(1);
});

it("leaves the last place alone when it points at another project", () => {
  writeLocalContext(HERE, { projectId: "kept", threadId: "t1" });
  forgetProject({ mode: "LOCAL", workspaceId: HERE }, "gone");
  expect(inspectLocalContext(HERE).context).toEqual({ projectId: "kept", threadId: "t1" });
});
