// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { readDraft, writeDraft } from "./conversation";
import { readResponseLanguageDefault, writeResponseLanguageDefault } from "./responseLanguageDefault";
import { submissionSignature, type PendingSubmission } from "./localWorkspacePersistence";
import { clearSubmission, readSubmission, writeSubmission } from "./submissionPersistence";

afterEach(() => { localStorage.clear(); sessionStorage.clear(); });
it("isolates hosted pending payloads and drafts by the server's opaque session scope", () => {
  const a = {mode: "HOSTED" as const, browserScopeId: "browser:aaaaaaaa"};
  const b = {mode: "HOSTED" as const, browserScopeId: "browser:bbbbbbbb"};
  const record: PendingSubmission = {version: 2, responseLanguage: "en", workspaceId: a.browserScopeId,
    projectId: "p", threadId: "t", problem: "same thread", selection: null,
    signature: submissionSignature("p", "t", "same thread", null, null, "en"),
    key: "11111111-1111-1111-1111-111111111111", createdAt: 1};
  expect(writeSubmission(a, record)).toBe("SAVED");
  expect(readSubmission(a, "p", "t")).toEqual({kind: "PENDING", value: record});
  expect(readSubmission(b, "p", "t")).toEqual({kind: "NONE"});
  expect(writeSubmission(b, record)).toBe("UNAVAILABLE");
  writeDraft("p", "t", "original draft", a);
  writeResponseLanguageDefault(a, "en");
  expect(readDraft("p", "t", a)).toBe("original draft");
  expect(readDraft("p", "t", b)).toBe("");
  expect(readResponseLanguageDefault(a)).toBe("en");
  expect(readResponseLanguageDefault(b)).toBeNull();
  expect(localStorage.length).toBe(0);
  expect(clearSubmission(b, "p", "t")).toBe(true);
  expect(readSubmission(a, "p", "t")).toEqual({kind: "PENDING", value: record});
});
