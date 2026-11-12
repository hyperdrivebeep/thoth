// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectSummary } from "../types";
import { ResourcesPage } from "./ProjectSessionPages";
import type { useCitationGate } from "./SourceCitationGate";
import type { useProjectSources } from "./useProjectSources";

// The sources page shows what is connected first: someone who opens it came to see their sources, not
// a web setting they cannot use yet.

const fixture = vi.hoisted(() => ({ consent: "UNDECIDED" }));
vi.mock("../api/rpcClient", async importOriginal => ({
  ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string) => {
    if (method === "workspace/setup/read") return { state: "SUCCEEDED", operation_id: "", value: { internet_consent: fixture.consent, internet_grant_id: null } };
    if (method === "project/read") return { state: "SUCCEEDED", operation_id: "", value: { revision: 1, policy: { payload: { public_web: { enabled: false, preferred_hosts: [] } } } } };
    throw new Error("unexpected method " + method);
  },
}));

type SourcesRead = ReturnType<typeof useProjectSources>["data"];
const project = { project_id: "p", name: "레이더 지연 분석", revision: 3 } as unknown as ProjectSummary;
const citation = { dialog: null, message: "", ask: vi.fn() } as unknown as ReturnType<typeof useCitationGate>;
const artifact = (id: string, name: string) => ({ artifact_id: id, project_id: "p", source_uri: "file:///" + name, media_type: "application/pdf",
  byte_sha256: "0".repeat(64), authority_state: "OFFICIAL", security_class: "INTERNAL", extraction_coverage: "FULL", cutoff_state: "ELIGIBLE" });
const sources = (artifacts: ReturnType<typeof artifact>[]) => ({ value: {
  artifacts, bindings: artifacts.map(item => ({ binding_id: "b:" + item.artifact_id, artifact_id: item.artifact_id, state: "ACTIVE" })), source_times: [],
}, state: "SUCCEEDED", operation_id: "op" }) as unknown as SourcesRead;

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
const openConsent = vi.fn();
async function mount(sourceData: SourcesRead, extra: { sourcesError?: Error; hosted?: boolean } = {}) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}>
    <ResourcesPage project={project} hosted={extra.hosted ?? false} citation={citation} sourceData={sourceData} sourcesError={extra.sourcesError ?? null} sourcesDenied={false}
      onPage={() => {}} onOpenConsent={extra.hosted ? undefined : openConsent} />
  </QueryClientProvider>));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  client?.clear();
  container?.remove();
  fixture.consent = "UNDECIDED";
  openConsent.mockClear();
});

const position = (selector: string) => {
  const all = [...container.querySelectorAll("*")];
  const found = container.querySelector(selector);
  expect(found, selector).not.toBeNull();
  return all.indexOf(found!);
};

it("puts the connected sources first, then adding a file, then the public web sources", async () => {
  await mount(sources([artifact("a", "radar-log.pdf"), artifact("b", "weather.pdf")]));
  const order = [position(".connected-sources"), position(".source-connect-panel"), position(".project-web-setup")];
  expect(order).toEqual([...order].sort((left, right) => left - right));
  const list = container.querySelector(".connected-sources")!;
  expect([...list.querySelectorAll("article.detail-card h3")].map(item => item.textContent)).toEqual(["radar-log.pdf", "weather.pdf"]);
  expect(list.textContent).not.toContain("연결한 자료가 없습니다");
});

it("says that nothing is connected yet and where to add a file, but not while the list is unread or failed", async () => {
  await mount(sources([]));
  expect(container.querySelector(".connected-sources")!.textContent).toContain("연결한 자료가 없습니다. 아래에서 파일을 올려 연결하세요.");
  await act(async () => root!.unmount()); container.remove();
  await mount(undefined);
  expect(container.querySelector(".connected-sources")!.textContent).not.toContain("연결한 자료가 없습니다");
  await act(async () => root!.unmount()); container.remove();
  await mount(sources([]), { sourcesError: new Error("자료 목록을 읽지 못했습니다") });
  expect(container.querySelector(".connected-sources")!.textContent).not.toContain("연결한 자료가 없습니다");
  expect(container.querySelector('.connected-sources [role="alert"]')!.textContent).toContain("자료 목록을 읽지 못했습니다");
});

it("leaves the web controls out while the workspace does not allow the internet, and gives one way to the setting", async () => {
  await mount(sources([]));
  const web = container.querySelector(".project-web-setup")!;
  expect(web.textContent).toContain("이 작업 공간에서는 인터넷 사용을 허용하지 않았습니다. 허용하면 여기서 조회할 도메인을 등록할 수 있습니다.");
  expect(web.querySelectorAll("input")).toHaveLength(0);
  const links = [...web.querySelectorAll("button")];
  expect(links.map(item => item.textContent)).toEqual(["설정에서 허용"]);
  await act(async () => { links[0].click(); });
  expect(openConsent).toHaveBeenCalledTimes(1);
});

it("shows the web controls once the workspace allows the internet", async () => {
  fixture.consent = "ALLOWED";
  await mount(sources([]));
  const web = container.querySelector(".project-web-setup")!;
  expect(web.querySelector("input[type='checkbox']")).not.toBeNull();
  expect(web.textContent).not.toContain("인터넷 사용을 허용하지 않았습니다");
  expect([...web.querySelectorAll("button")].map(item => item.textContent)).not.toContain("설정에서 허용");
});

it("offers no way to the setting where this screen cannot change it (hosted review)", async () => {
  await mount(sources([]), { hosted: true });
  const web = container.querySelector(".project-web-setup")!;
  expect(web.textContent).toContain("이 작업 공간에서는 인터넷 사용을 허용하지 않았습니다.");
  expect(web.textContent).not.toContain("허용하면 여기서");
  expect(web.querySelectorAll("button")).toHaveLength(0);
});

