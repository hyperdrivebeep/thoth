import { describe, expect, it } from "vitest";
import {
  executionLabel,
  normalizePreferredHosts,
  parsePublicWebUpdate,
  publicWebExecutionSchema,
  publicWebUpdateInputSchema,
} from "./publicWeb";

describe("public web update contract", () => {
  it("requires a strict boolean and normalizes hosts", () => {
    expect(() => publicWebUpdateInputSchema.parse({ enabled: "true", preferred_hosts: ["arxiv.org"] })).toThrow();
    const parsed = parsePublicWebUpdate({
      enabled: true,
      preferred_hosts: ["https://ArXiv.org/list", "arxiv.org"],
    });
    expect(parsed.enabled).toBe(true);
    expect(parsed.preferred_hosts).toEqual(["arxiv.org"]);
  });

  it("rejects an enabled save with no hosts", () => {
    expect(() => parsePublicWebUpdate({ enabled: true, preferred_hosts: ["  "] })).toThrow(
      /PUBLIC_WEB_HOSTS_REQUIRED/,
    );
    expect(parsePublicWebUpdate({ enabled: false, preferred_hosts: [] })).toEqual({
      enabled: false,
      preferred_hosts: [],
    });
  });

  it("does not let the client mint execution permission fields", () => {
    expect(() =>
      publicWebUpdateInputSchema.parse({
        enabled: true,
        preferred_hosts: ["arxiv.org"],
        allowed_hosts: ["example.org"],
        query_security_class: "PUBLIC",
        state: "READY",
      }),
    ).toThrow();
  });
});

describe("public web execution status", () => {
  it("keeps typed execution states separate from the saved toggle", () => {
    expect(publicWebExecutionSchema.parse({ desired_enabled: true, state: "READY" }).state).toBe("READY");
    expect(executionLabel("READY")).toBe("실행: 준비됨");
    expect(executionLabel("OFF")).toBe("실행: 꺼짐");
    expect(executionLabel("BLOCKED", ["PUBLIC_WEB_POLICY_INCONSISTENT"])).toBe("실행: 권한 불일치");
    expect(normalizePreferredHosts(["HTTPS://ArXiv.org/", "arxiv.org"])).toEqual(["arxiv.org"]);
  });
});
