import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "../i18n/locale";
import {
  credentialAvailabilityNote,
  credentialConnectionHint,
  guidanceText,
  type CredentialAccount,
} from "./modelCredentialPresentation";

afterEach(() => setLocale("ko"));

// A sentence that says "the real model run is not confirmed yet", in any of the wordings the screen has used.
const UNCONFIRMED =
  /실행 성공은 별도|별도 확인이 필요|첫 연구에서 확인|(확인|검증)되지 않았|미검증/;
const sentences = (text: string | null) => (text ?? "").split(/(?<=[.])\s+/).filter(Boolean);
const unconfirmed = (text: string | null) =>
  sentences(text).filter((sentence) => UNCONFIRMED.test(sentence));

const chatgpt: CredentialAccount = {
  provider: "openai",
  label: "ChatGPT",
  connected: true,
  has_key: false,
  oauth: true,
  login_supported: true,
  login_kind: "codex_isolated_browser",
  connection_state: "EXECUTION_UNVERIFIED",
  profile_mode: "THOTH_ISOLATED",
  execution_eligible: true,
  execution_verified: false,
  remote_auth_verified: null,
  available_model_providers: ["codex-oauth"],
  guidance: "The Codex route is eligible; live execution has not been verified",
};
const claude: CredentialAccount = {
  provider: "anthropic",
  label: "Claude",
  connected: true,
  has_key: false,
  oauth: true,
  login_supported: true,
  connection_state: "EXECUTION_UNVERIFIED",
  execution_eligible: true,
  execution_verified: false,
  remote_auth_verified: null,
  available_model_providers: ["claude-code"],
  guidance: "Sign in with the official Claude Code executable in the THOTH profile",
  auth_methods: [
    {
      auth_method: "claude_code_login",
      route: "claude-code",
      connected: true,
      execution_eligible: true,
      capabilities: { start: true },
    },
  ],
};
const xai: CredentialAccount = {
  provider: "xai",
  label: "xAI",
  connected: true,
  has_key: false,
  oauth: true,
  login_supported: true,
  login_kind: "xai_device_code",
  connection_state: "EXECUTION_UNVERIFIED",
  profile_mode: "THOTH_XAI_OAUTH",
  execution_eligible: true,
  execution_verified: false,
  remote_auth_verified: null,
  available_model_providers: ["xai-oauth"],
  guidance: "Use the THOTH workspace xAI device login; remote execution is unverified",
};

describe("the account note says once that a real run is not confirmed", () => {
  it.each([
    ["ChatGPT", chatgpt],
    ["Claude", claude],
    ["xAI", xai],
  ] as const)("%s: one such sentence in the note and the hint together", (_name, account) => {
    const note = credentialAvailabilityNote(account);
    const everything = [note, credentialConnectionHint(account)].join(" ");
    expect(unconfirmed(note), note ?? "").toHaveLength(1);
    expect(unconfirmed(everything), everything).toHaveLength(1);
  });

  it("keeps the state line and the reason that are not repeats", () => {
    const note = credentialAvailabilityNote(chatgpt) ?? "";
    expect(note).toContain("THOTH 전용 별도 프로필");
    expect(note).toContain("Codex 모델 연결은 실험적입니다");
    expect(note).toContain(
      "Codex 경로는 사용을 시도할 수 있지만 실제 실행은 아직 확인되지 않았습니다",
    );
  });

  it("without the server's guidance it says it from the connection state", () => {
    const note = credentialAvailabilityNote({ ...chatgpt, guidance: null });
    expect(unconfirmed(note)).toEqual([
      "로컬 실행 시도 조건은 확인됐지만 실제 공급자 성공은 아직 검증되지 않았습니다.",
    ]);
    const connected = credentialAvailabilityNote({
      ...chatgpt,
      guidance: null,
      connection_state: undefined,
      execution_verified: undefined,
    });
    expect(unconfirmed(connected)).toEqual(["실제 공급자 호출은 아직 확인되지 않았습니다."]);
  });

  it("says nothing about a real run for an account that is not signed in", () => {
    const note = credentialAvailabilityNote({
      ...chatgpt,
      connected: false,
      connection_state: "LOGIN_REQUIRED",
      guidance: null,
      execution_eligible: null,
    });
    expect(unconfirmed(note)).toHaveLength(0);
    expect(note).toContain("로그인을 완료한 뒤 연결 상태를 다시 확인하세요");
  });

  it("says once that sign-in is needed when the server's hint repeats it", () => {
    const signedOut = { ...chatgpt, connected: false, oauth: false, execution_eligible: null };
    const required =
      credentialAvailabilityNote({
        ...signedOut,
        connection_state: "LOGIN_REQUIRED",
        guidance: "Connect the THOTH-only Codex profile",
      }) ?? "";
    expect(sentences(required).filter((sentence) => sentence.includes("로그인"))).toEqual([
      "로그인을 완료한 뒤 연결 상태를 다시 확인하세요.",
    ]);
    const pending =
      credentialAvailabilityNote({
        ...signedOut,
        connection_state: "LOGIN_PENDING",
        guidance: "Finish the THOTH Codex sign-in, then check connection status",
      }) ?? "";
    expect(pending).not.toContain("THOTH 전용 Codex 로그인을 마친 뒤");
    expect(pending).toContain("시작 응답은 연결 완료가 아닙니다");
    const claudeSignedOut =
      credentialAvailabilityNote({
        ...claude,
        connected: false,
        oauth: false,
        connection_state: "LOGIN_REQUIRED",
      }) ?? "";
    expect(
      sentences(claudeSignedOut).filter((sentence) => sentence.includes("로그인하세요")),
    ).toEqual([]);
    expect(claudeSignedOut).toContain("로그인을 완료한 뒤 연결 상태를 다시 확인하세요.");
  });

  it("keeps a hint that says something the state does not", () => {
    const note =
      credentialAvailabilityNote({
        ...chatgpt,
        connected: false,
        oauth: false,
        execution_eligible: null,
        connection_state: "LOGIN_REQUIRED",
        guidance: "Check the isolated Codex model catalog",
      }) ?? "";
    expect(note).toContain("THOTH 전용 Codex의 모델 목록을 확인하세요.");
  });

  it("ends the server's English hint with a full stop, so the next sentence does not run into it", () => {
    setLocale("en");
    expect(guidanceText("Check the isolated Codex model catalog")).toBe(
      "Check the isolated Codex model catalog.",
    );
    const note =
      credentialAvailabilityNote({ ...claude, connection_state: "EXECUTION_UNVERIFIED" }) ?? "";
    expect(note).not.toMatch(/[a-z] [A-Z][a-z]+ model execution/);
    expect(note).toContain("in the THOTH profile. ");
  });

  it("writes Japanese and Chinese notes without a space after the full stop", () => {
    for (const code of ["ja", "zh-Hans", "zh-Hant"] as const) {
      setLocale(code);
      const signedOut =
        credentialAvailabilityNote({
          ...chatgpt,
          connected: false,
          oauth: false,
          connection_state: "LOGIN_REQUIRED",
          guidance: null,
        }) ?? "";
      const signedIn = credentialAvailabilityNote(chatgpt) ?? "";
      for (const note of [signedOut, signedIn]) {
        expect(note.split("。").length, code).toBeGreaterThan(2);
        expect(note, code).not.toMatch(/。\s/);
      }
    }
  });
});
