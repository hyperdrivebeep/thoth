// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { readDraft, writeDraft } from "./conversation";
import { readWorkspaceContext, saveWorkspaceContext } from "./research";
import { clearLocalPending, inspectLocalContext, inspectLocalDraft, readLocalPending, submissionSignature, writeLocalPending, type BrowserScope, type PendingSubmission } from "./localWorkspacePersistence";

const a: BrowserScope = { mode: "LOCAL", workspaceId: `workspace:${"a".repeat(32)}` };
const b: BrowserScope = { mode: "LOCAL", workspaceId: `workspace:${"b".repeat(32)}` };
const hosted: BrowserScope = { mode: "HOSTED" };

afterEach(() => { localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); });

it("restores only the selected LOCAL workspace context after a browser restart", () => {
  sessionStorage.setItem("thoth:web-context:v1", JSON.stringify({ version: 1, projectId: "legacy", threadId: "legacy" }));
  expect(saveWorkspaceContext("project-a", "thread-a", a)).toBe(true);
  expect(saveWorkspaceContext("project-b", "thread-b", b)).toBe(true);
  expect(readWorkspaceContext(a)).toEqual({ projectId: "project-a", threadId: "thread-a" });
  expect(readWorkspaceContext(b)).toEqual({ projectId: "project-b", threadId: "thread-b" });
  expect(readWorkspaceContext(hosted)).toMatchObject({ projectId: "legacy", threadId: "legacy" });
  expect(readWorkspaceContext({ mode: "LOCAL", workspaceId: null })).toEqual({ projectId: "", threadId: "" });
});

it("isolates unsent drafts by workspace, project and thread without borrowing the Hosted draft", () => {
  writeDraft("p", "t", "hosted draft", hosted);
  expect(writeDraft("p", "t", "local A draft", a)).toBe("SAVED");
  expect(writeDraft("p", "t", "local B draft", b)).toBe("SAVED");
  expect(readDraft("p", "t", a)).toBe("local A draft");
  expect(readDraft("p", "t", b)).toBe("local B draft");
  expect(readDraft("p", "other", a)).toBe("");
  expect(readDraft("p", "t", hosted)).toBe("hosted draft");
  expect(readDraft("p", "t", { mode: "LOCAL", workspaceId: null })).toBe("");
});

it("does not truncate oversized drafts or erase the last saved version on storage failure", () => {
  expect(writeDraft("p", "t", "saved", a)).toBe("SAVED");
  expect(writeDraft("p", "t", "x".repeat(20_001), a)).toBe("TOO_LARGE");
  expect(readDraft("p", "t", a)).toBe("saved");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
  expect(writeDraft("p", "t", "new text", a)).toBe("UNAVAILABLE");
  expect(readDraft("p", "t", a)).toBe("saved");
});

it("ignores corrupt LOCAL context and draft records without consulting shared session keys", () => {
  localStorage.setItem(`thoth:local-workspace:v1:context:${a.mode === "LOCAL" ? a.workspaceId : ""}`, "{bad-json");
  expect(readWorkspaceContext(a)).toEqual({ projectId: "", threadId: "" });
  expect(inspectLocalContext(a.mode === "LOCAL" ? a.workspaceId : null).kind).toBe("CORRUPT");
  localStorage.setItem(`thoth:local-workspace:v1:draft:${JSON.stringify([a.mode === "LOCAL" ? a.workspaceId : "", "p", "t"])}`, "{bad-json");
  expect(readDraft("p", "t", a)).toBe("");
  expect(inspectLocalDraft(a.mode === "LOCAL" ? a.workspaceId : null, "p", "t").kind).toBe("CORRUPT");
});

it("distinguishes unreadable browser storage from a missing or corrupt draft", () => {
  const workspaceId = a.mode === "LOCAL" ? a.workspaceId : null;
  expect(inspectLocalDraft(workspaceId, "p", "t").kind).toBe("MISSING");
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("storage denied"); });
  expect(inspectLocalDraft(workspaceId, "p", "t").kind).toBe("UNAVAILABLE");
  expect(inspectLocalContext(workspaceId).kind).toBe("UNAVAILABLE");
});

it("pins a bounded pending key to one workspace and exact submitted payload", () => {
  const value: PendingSubmission = { version: 1, workspaceId: a.mode === "LOCAL" ? a.workspaceId! : "", projectId: "p", threadId: "t",
    problem: "synthetic question", selection: { provider: "test", model: "test", reasoning_effort: "high" },
    signature: submissionSignature("p", "t", "synthetic question", { provider: "test", model: "test", reasoning_effort: "high" }),
    key: "11111111-1111-4111-8111-111111111111", createdAt: 1 };
  expect(writeLocalPending(value)).toBe("SAVED");
  expect(readLocalPending(a.mode === "LOCAL" ? a.workspaceId : null, "p", "t")).toEqual({ kind: "PENDING", value });
  expect(readLocalPending(b.mode === "LOCAL" ? b.workspaceId : null, "p", "t")).toEqual({ kind: "NONE" });
  expect(readLocalPending(a.mode === "LOCAL" ? a.workspaceId : null, "p", "other")).toEqual({ kind: "NONE" });
  expect(writeLocalPending({ ...value, signature: "different payload" })).toBe("UNAVAILABLE");
  expect(writeLocalPending({ ...value, problem: "x".repeat(20_001), signature: submissionSignature("p", "t", "x".repeat(20_001), value.selection) })).toBe("TOO_LARGE");
  expect(clearLocalPending(a.mode === "LOCAL" ? a.workspaceId : null, "p", "t")).toBe(true);
  expect(readLocalPending(a.mode === "LOCAL" ? a.workspaceId : null, "p", "t")).toEqual({ kind: "NONE" });
});

it("keeps a trace-row origin with the pending request, and a different origin is a different request", () => {
  const id = a.mode === "LOCAL" ? a.workspaceId : null;
  const origin = { kind: "TRACE_VERDICT" as const, project_id: "p", subject_kind: "CRITERION" as const, subject_id: "C-1", verdict_revision: "a".repeat(64) };
  const value: PendingSubmission = { version: 1, workspaceId: id!, projectId: "p", threadId: "t", problem: "synthetic question", selection: null, origin,
    signature: submissionSignature("p", "t", "synthetic question", null, origin), key: "11111111-1111-4111-8111-111111111111", createdAt: 1 };
  expect(writeLocalPending(value)).toBe("SAVED");
  expect(readLocalPending(id, "p", "t")).toEqual({ kind: "PENDING", value });
  expect(submissionSignature("p", "t", "synthetic question", null, origin)).not.toBe(submissionSignature("p", "t", "synthetic question", null));
  expect(submissionSignature("p", "t", "synthetic question", null, null)).toBe(submissionSignature("p", "t", "synthetic question", null)); // no origin: the old signature
  expect(writeLocalPending({ ...value, signature: submissionSignature("p", "t", "synthetic question", null) })).toBe("UNAVAILABLE"); // the origin is part of the exact payload
  // a made-up origin shape is not a pending request
  localStorage.setItem(`thoth:local-workspace:v1:pending:${JSON.stringify([id, "p", "t"])}`, JSON.stringify({ ...value, origin: { ...origin, extra: 1 } }));
  expect(readLocalPending(id, "p", "t")).toEqual({ kind: "INVALID" });
  clearLocalPending(id, "p", "t");
});

it("blocks a corrupt pending marker instead of assigning a new key", () => {
  const workspaceId = a.mode === "LOCAL" ? a.workspaceId : "";
  localStorage.setItem(`thoth:local-workspace:v1:pending:${JSON.stringify([workspaceId, "p", "t"])}`, "{broken");
  expect(readLocalPending(workspaceId, "p", "t")).toEqual({ kind: "INVALID" });
});
