// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectWebSetup } from "./ProjectWebSetup";

type ProjectValue = {
  revision: number;
  policy?: { payload?: { public_web?: { enabled?: boolean; preferred_hosts?: string[] } } };
  public_web_execution?: {
    desired_enabled?: boolean;
    state?: string;
    reason_codes?: string[];
    effective_hosts?: string[];
  };
};

const fixture = vi.hoisted(() => ({
  calls: [] as { method: string; input: Record<string, unknown>; key: string }[],
  projects: {} as Record<string, ProjectValue>,
  consent: "ALLOWED" as string,
  grant: "grant-1" as string | null,
  delay: false,
  pending: null as null | (() => void),
}));

vi.mock("../api/rpcClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>, key: string) => {
    fixture.calls.push({ method, input, key });
    if (fixture.delay && method === "project/read") {
      await new Promise<void>((resolve) => {
        fixture.pending = resolve;
      });
    }
    if (method === "workspace/setup/read") {
      return {
        state: "SUCCEEDED",
        operation_id: "",
        value: { internet_consent: fixture.consent, internet_grant_id: fixture.grant },
      };
    }
    if (method === "project/read") {
      const projectId = String(input.project_id);
      return { state: "SUCCEEDED", operation_id: "", value: fixture.projects[projectId] };
    }
    if (method === "project/policy/update") {
      const projectId = String(input.project_id);
      const payload = input.payload as { public_web: { enabled: boolean; preferred_hosts: string[] } };
      const current = fixture.projects[projectId];
      fixture.projects[projectId] = {
        revision: current.revision + 1,
        policy: { payload: { public_web: payload.public_web } },
        public_web_execution: {
          desired_enabled: payload.public_web.enabled,
          state: payload.public_web.enabled ? "READY" : "OFF",
          reason_codes: [],
          effective_hosts: payload.public_web.preferred_hosts,
        },
      };
      return { state: "SUCCEEDED", operation_id: "", value: fixture.projects[projectId] };
    }
    throw new Error(`unexpected method ${method}`);
  },
}));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
async function flush() {
  await act(async () => {
    await tick();
  });
}

async function mount(projectId: string) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  if (root) await act(async () => root.unmount());
  container?.remove();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ProjectWebSetup projectId={projectId} />
      </QueryClientProvider>,
    );
  });
  await flush();
}

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  client?.clear();
  container?.remove();
  fixture.calls = [];
  fixture.delay = false;
  fixture.pending = null;
  fixture.consent = "ALLOWED";
  fixture.grant = "grant-1";
});

it("separates a saved ON toggle from execution READY", async () => {
  fixture.calls = [];
  fixture.consent = "ALLOWED";
  fixture.projects = {
    p: {
      revision: 1,
      policy: { payload: { public_web: { enabled: true, preferred_hosts: ["arxiv.org"] } } },
      public_web_execution: {
        desired_enabled: true,
        state: "READY",
        reason_codes: [],
        effective_hosts: ["arxiv.org"],
      },
    },
  };
  await mount("p");
  expect(container.textContent).toContain("허용할 공개 웹 출처");
  expect(container.querySelector<HTMLInputElement>('[aria-label="허용할 공개 웹 도메인"]')?.placeholder).toBe("예: arxiv.org");
  expect(container.textContent).toContain("실행: 준비됨");
  expect(fixture.calls.map((call) => call.method).sort()).toEqual([
    "project/read",
    "workspace/setup/read",
  ]);
  expect(fixture.calls.some((call) => /discover|acquire|search/i.test(call.method))).toBe(false);
});

it("offers a reapply path for a legacy ON project that is blocked", async () => {
  fixture.calls = [];
  fixture.consent = "ALLOWED";
  fixture.projects = {
    p: {
      revision: 4,
      policy: { payload: { public_web: { enabled: true, preferred_hosts: ["arxiv.org"] } } },
      public_web_execution: {
        desired_enabled: true,
        state: "BLOCKED",
        reason_codes: ["PUBLIC_WEB_POLICY_INCONSISTENT"],
        effective_hosts: ["arxiv.org"],
      },
    },
  };
  await mount("p");
  expect(container.textContent).toContain("실행: 권한 불일치");
  const reapply = Array.from(container.querySelectorAll("button")).find((item) =>
    item.textContent?.includes("저장된 웹 설정 다시 적용"),
  );
  expect(reapply).toBeTruthy();
  await act(async () => {
    reapply!.click();
    await tick();
  });
  const saved = fixture.calls.filter((call) => call.method === "project/policy/update");
  expect(saved).toHaveLength(1);
  expect(saved[0]?.input.payload).toEqual({
    public_web: {
      enabled: true,
      preferred_hosts: ["arxiv.org"],
      workspace_grant_id: "grant-1",
    },
  });
  expect(fixture.calls.some((call) => /discover|acquire|search/i.test(call.method))).toBe(false);
});

it("does not allow turning the web on without hosts, and still allows OFF after consent is revoked", async () => {
  fixture.calls = [];
  fixture.consent = "DENIED";
  fixture.projects = {
    p: {
      revision: 1,
      policy: { payload: { public_web: { enabled: false, preferred_hosts: [] } } },
      public_web_execution: { desired_enabled: false, state: "OFF", reason_codes: [] },
    },
  };
  await mount("p");
  const checkbox = container.querySelector<HTMLInputElement>("input[type='checkbox']")!;
  expect(checkbox.disabled).toBe(true);
  fixture.projects = {
    p: {
      revision: 2,
      policy: { payload: { public_web: { enabled: true, preferred_hosts: ["arxiv.org"] } } },
      public_web_execution: {
        desired_enabled: true,
        state: "BLOCKED",
        reason_codes: ["PUBLIC_WEB_CONSENT_REQUIRED"],
      },
    },
  };
  await mount("p");
  const enabled = container.querySelector<HTMLInputElement>("input[type='checkbox']")!;
  expect(enabled.disabled).toBe(false);
  await act(async () => {
    enabled.click();
    await tick();
  });
  const saved = fixture.calls.filter((call) => call.method === "project/policy/update").at(-1);
  expect(saved?.input.payload).toMatchObject({
    public_web: { enabled: false, preferred_hosts: ["arxiv.org"] },
  });
});

it("does not apply a late project read to a different project", async () => {
  fixture.calls = [];
  fixture.consent = "ALLOWED";
  fixture.projects = {
    p: {
      revision: 1,
      policy: { payload: { public_web: { enabled: true, preferred_hosts: ["arxiv.org"] } } },
      public_web_execution: {
        desired_enabled: true,
        state: "READY",
        reason_codes: [],
        effective_hosts: ["arxiv.org"],
      },
    },
    q: {
      revision: 1,
      policy: { payload: { public_web: { enabled: false, preferred_hosts: ["example.org"] } } },
      public_web_execution: { desired_enabled: false, state: "OFF", reason_codes: [] },
    },
  };
  fixture.delay = true;
  await mount("p");
  fixture.delay = false;
  await mount("q");
  await act(async () => {
    fixture.pending?.();
    await tick();
  });
  expect(container.textContent).toContain("실행: 꺼짐");
  expect(container.textContent).not.toContain("arxiv.org");
  expect(container.textContent).toContain("example.org");
});
