// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { readLocalPending, submissionSignature, writeLocalPending, type PendingSubmission } from "./localWorkspacePersistence";
import { readResponseLanguageDefault, writeResponseLanguageDefault } from "./responseLanguageDefault";
import { setLocale } from "../i18n/locale";

afterEach(() => { localStorage.clear(); setLocale("ko"); });
const scope = { mode: "LOCAL" as const, workspaceId: "workspace:language-test" };
const fields = { workspaceId: scope.workspaceId, projectId: "p", threadId: "t", problem: "Question", selection: null,
  key: "11111111-1111-1111-1111-111111111111", createdAt: 1 };

it("preserves v1 signatures and keeps explicit ko distinct from omission", () => {
  const legacy = submissionSignature("p", "t", "Question", null);
  expect(legacy).toBe('["p","t","Question",null]');
  expect(submissionSignature("p", "t", "Question", null, null, "ko")).not.toBe(legacy);
  const saved: PendingSubmission = { ...fields, version: 1, signature: legacy };
  expect(writeLocalPending(saved)).toBe("SAVED");
  setLocale("en");
  expect(readLocalPending(scope.workspaceId, "p", "t")).toEqual({ kind: "PENDING", value: saved });
});

it("replays v2 with its saved language regardless of display and default-language changes", () => {
  const saved: PendingSubmission = { ...fields, version: 2, responseLanguage: "en",
    signature: submissionSignature("p", "t", "Question", null, null, "en") };
  expect(writeLocalPending(saved)).toBe("SAVED");
  writeResponseLanguageDefault(scope, "ko");
  setLocale("en");
  expect(readResponseLanguageDefault(scope)).toBe("ko");
  expect(readLocalPending(scope.workspaceId, "p", "t")).toEqual({ kind: "PENDING", value: saved });
});
