// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { LANGUAGE_CHOICES } from "../i18n/languageChoices";
import { readResponseLanguageDefault, writeResponseLanguageDefault } from "./responseLanguageDefault";
import { submissionSignature, type PendingSubmission } from "./localWorkspacePersistence";
import { isResponseLanguage, knownResponseLanguages, RESPONSE_LANGUAGE_CHOICES, responseLanguageLabel } from "./responseLanguages";
import { clearSubmission, readSubmission, writeSubmission } from "./submissionPersistence";
import { resultLanguage } from "../i18n/ResponseLanguageContext";

afterEach(() => { localStorage.clear(); sessionStorage.clear(); });

it("lists the answer languages in one place, apart from the screen languages", () => {
  expect(RESPONSE_LANGUAGE_CHOICES.map(choice => choice.code)).toEqual(["ko", "en", "zh-Hans"]);
  expect(responseLanguageLabel("zh-Hans")).toBe("中文(简体)");
  expect(LANGUAGE_CHOICES.map(choice => choice.code)).toEqual(["ko", "en", "zh-Hans"]);
  expect(RESPONSE_LANGUAGE_CHOICES.map(choice => choice.label)).toEqual(LANGUAGE_CHOICES.map(choice => choice.label));
});

it("accepts only listed languages, and keeps from a server's list only the ones this app can offer", () => {
  expect(["ko", "en", "zh-Hans"].every(isResponseLanguage)).toBe(true);
  expect(["ja", "zh", "zh-Hant", "", null, 1].some(isResponseLanguage)).toBe(false);
  expect(knownResponseLanguages(["ko", "ja", "zh-Hans", 3])).toEqual(["ko", "zh-Hans"]);
  expect(knownResponseLanguages(undefined)).toEqual([]);
  expect(resultLanguage({ response_language: "zh-Hans" })).toBe("zh-Hans");
  expect(resultLanguage({ response_language: "ja" })).toBeNull();
});

it("keeps a Chinese default and a Chinese pending request, and refuses an unlisted language", () => {
  const scope = { mode: "LOCAL" as const, workspaceId: "workspace:zh-hans" };
  writeResponseLanguageDefault(scope, "zh-Hans");
  expect(readResponseLanguageDefault(scope)).toBe("zh-Hans");
  const pending: PendingSubmission = { version: 2, responseLanguage: "zh-Hans", workspaceId: scope.workspaceId,
    projectId: "p", threadId: "t", problem: "中文问题", selection: null,
    signature: submissionSignature("p", "t", "中文问题", null, null, "zh-Hans"),
    key: "11111111-1111-1111-1111-111111111111", createdAt: 1 };
  expect(writeSubmission(scope, pending)).toBe("SAVED");
  expect(readSubmission(scope, "p", "t")).toEqual({ kind: "PENDING", value: pending });
  expect(clearSubmission(scope, "p", "t")).toBe(true);
  const unlisted = { ...pending, responseLanguage: "ja" } as unknown as PendingSubmission;
  expect(writeSubmission(scope, unlisted)).toBe("UNAVAILABLE");
});
