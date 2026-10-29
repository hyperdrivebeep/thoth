// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResearchSession } from "./useResearchSession";
import { LanguageSettingsCard } from "./LanguageSettingsCard";
import { ResearchComposer } from "./ResearchComposer";
import { getDisplayPreference, getLocale, setDisplayPreference, setLocale, t } from "../i18n/locale";
import { LANGUAGE_CHOICES } from "../i18n/languageChoices";
import { readResponseLanguageDefault } from "../api/responseLanguageDefault";
import type { BrowserScope } from "../api/localWorkspacePersistence";

const state = vi.hoisted(() => ({ accepted: "ko" as string | null, calls: [] as { method: string; params: Record<string, unknown> }[] }));
vi.mock("../api/rpcClient", () => ({ rpc: async (method: string, params: Record<string, unknown>) => {
  state.calls.push({ method, params });
  if (method === "thread/read") return { value: { operation_state: "SUCCEEDED", problem: "Earlier question", request: { operation_id: "operation:old", ...(state.accepted ? { response_language: state.accepted } : {}) } } };
  if (method === "thread/input" || method === "thread/start") {
    return { state: "RUNNING", operation_id: "operation:new", value: { contract_version: 2, project_id: "p", thread_id: "t",
      status: "ACCEPTED_RUNNING", operation_id: "operation:new", request_epoch: 2 } };
  }
  throw new Error("Unexpected call " + method);
} }));

const scope: BrowserScope = { mode: "LOCAL", workspaceId: "workspace:language-settings" };
let session: ReturnType<typeof useResearchSession>;
let opened = 0;
let root: Root;
let node: HTMLDivElement;
let client: QueryClient;

function Harness({ composer = false }: { composer?: boolean }) {
  const epoch = useRef(0);
  session = useResearchSession("p", "t", epoch, () => {}, scope, true);
  return <>
    <LanguageSettingsCard scope={scope} />
    {composer && <ResearchComposer session={session} projectId="p" threadId="t" epoch={0} onAttach={() => {}} onOpenSettings={() => { opened += 1; }} />}
  </>;
}
const ready = (languages: string[] | null) => ({ value: languages ? { response_languages: languages, response_language_scope: "REQUEST" } : {} });
async function mount(languages: string[] | null = ["ko", "en"], composer = false) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["workspace-ready"], ready(languages));
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => { root.render(<QueryClientProvider client={client}><Harness composer={composer} /></QueryClientProvider>); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
const select = (id: string) => document.getElementById(id) as HTMLSelectElement;
async function choose(id: string, value: string) {
  const element = select(id);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
    setter.call(element, value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
const sent = () => state.calls.filter(call => call.method === "thread/input");
async function send(text: string) {
  await act(async () => { session.setProblem(text); });
  await act(async () => { session.send(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); sessionStorage.clear(); state.calls.length = 0; state.accepted = "ko"; opened = 0; setLocale("ko");
});
afterEach(async () => {
  await act(async () => root.unmount()); client.clear(); node.remove(); localStorage.clear(); sessionStorage.clear(); setLocale("ko");
  Object.defineProperty(globalThis.navigator, "languages", { value: ["ko-KR", "ko"], configurable: true });
});

describe("response language default in Settings", () => {
  it("lists the languages from the one constant, with automatic first", async () => {
    await mount();
    const labels = [...select("reply-language-default").options].map(option => option.textContent);
    expect(labels).toEqual([t("language.replyAuto"), ...LANGUAGE_CHOICES.map(choice => choice.label)]);
    expect([...select("display-language").options].map(option => option.value)).toEqual(["auto", ...LANGUAGE_CHOICES.map(choice => choice.code)]);
    expect(document.body.textContent).toContain(t("language.replyAutoHelp"));
  });

  it.each(["ko", "en"] as const)("a fixed %s in Settings is sent with the next question", async language => {
    await mount();
    await choose("reply-language-default", language);
    expect(session.responseLanguage).toBe(language);
    await send("Question to answer in the chosen language");
    expect(sent()).toHaveLength(1);
    expect(sent()[0].params.response_language).toBe(language);
  });

  it("automatic leaves response_language out of the request, whatever the accepted request said", async () => {
    await mount();
    await choose("reply-language-default", "en");
    await choose("reply-language-default", "auto");
    expect(session.responseLanguage).toBeNull();
    await send("Question in whatever language it is");
    expect(sent()).toHaveLength(1);
    expect("response_language" in sent()[0].params).toBe(false);
  });

  it("without server support the fixed choices are disabled with the reason, and automatic stays available", async () => {
    await mount(null);
    const options = [...select("reply-language-default").options];
    expect(options.find(option => option.value === "auto")!.disabled).toBe(false);
    expect(options.filter(option => option.value !== "auto").every(option => option.disabled)).toBe(true);
    expect(document.body.textContent).toContain(t("language.unsupported"));
    expect(session.languageSupported).toBe(false);
    await send("Question that needs no language");
    expect("response_language" in sent()[0].params).toBe(false);
  });

  it("a fixed choice stays after reload, in one key for the scope, and leaves the old per-conversation keys alone", async () => {
    const legacy = "thoth:draft-language:v1:" + JSON.stringify(["workspace:language-settings", "p", "t"]);
    localStorage.setItem(legacy, "en");
    await mount();
    await choose("reply-language-default", "ko");
    expect(readResponseLanguageDefault(scope)).toBe("ko");
    const keys = Object.keys(localStorage).filter(key => key.startsWith("thoth:response-language-default:v1:"));
    expect(keys).toHaveLength(1);
    await act(async () => root.unmount()); client.clear(); node.remove();
    await mount();
    expect(select("reply-language-default").value).toBe("ko");
    expect(session.responseLanguage).toBe("ko");
    expect(localStorage.getItem(legacy)).toBe("en");
  });

  it("the old per-conversation choice is not read", async () => {
    localStorage.setItem("thoth:draft-language:v1:" + JSON.stringify(["workspace:language-settings", "p", "t"]), "en");
    await mount();
    expect(session.responseLanguage).toBeNull();
    expect(select("reply-language-default").value).toBe("auto");
  });
});

describe("question box", () => {
  it("shows nothing about the response language while automatic, and has no language select", async () => {
    await mount(["ko", "en"], true);
    expect(document.querySelector(".language-fixed")).toBeNull();
    expect(node.querySelector(".composer-dock select#reply-language-default")).toBeNull();
    const optionTexts = [...node.querySelectorAll(".composer-dock select option")].map(item => item.textContent);
    for (const choice of LANGUAGE_CHOICES) expect(optionTexts).not.toContain(choice.label);
    expect(document.querySelectorAll("select#reply-language-default")).toHaveLength(1);
  });

  it("shows a fixed language with a way to Settings, in either screen language", async () => {
    await mount(["ko", "en"], true);
    await choose("reply-language-default", "en");
    const badge = document.querySelector(".language-fixed")!;
    expect(badge.textContent).toContain("답변: English 고정");
    await act(async () => { (badge.querySelector("button") as HTMLButtonElement).click(); });
    expect(opened).toBe(1);
    await act(async () => { setLocale("en"); });
    expect(document.querySelector(".language-fixed")!.textContent).toContain("Reply: English (fixed)");
    expect(document.querySelector(".language-fixed")!.textContent).toContain(t("language.changeInSettings"));
  });

  it("still names the language of the accepted request when it differs from the setting", async () => {
    state.accepted = "ko";
    await mount(["ko", "en"], true);
    await choose("reply-language-default", "en");
    expect(document.querySelector(".language-current")!.textContent).toContain(t("language.acceptedShort") + ": 한국어");
    await choose("reply-language-default", "ko");
    expect(document.querySelector(".language-current")).toBeNull();
  });
});

describe("display language", () => {
  const browser = (...languages: string[]) => Object.defineProperty(globalThis.navigator, "languages", { value: languages, configurable: true });

  it("automatic follows the browser language and the first supported one wins", async () => {
    await mount();
    browser("en-US", "ko");
    await choose("display-language", "auto");
    expect(getDisplayPreference()).toBe("auto");
    expect(getLocale()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    browser("ja-JP", "ko-KR");
    await act(async () => { setDisplayPreference("auto"); });
    expect(getLocale()).toBe("ko");
    browser("fr-FR");
    await act(async () => { setDisplayPreference("auto"); });
    expect(getLocale()).toBe("ko");
  });

  it("an explicit choice ignores the browser and a language chosen before is kept", async () => {
    browser("en-US");
    await mount();
    await choose("display-language", "ko");
    expect(getLocale()).toBe("ko");
    expect(localStorage.getItem("thoth:display-locale:v1")).toBe("ko");
    vi.resetModules();
    localStorage.setItem("thoth:display-locale:v1", "en");
    browser("ko-KR");
    const fresh = await import("../i18n/locale");
    expect(fresh.getDisplayPreference()).toBe("en");
    expect(fresh.getLocale()).toBe("en");
    localStorage.removeItem("thoth:display-locale:v1");
    vi.resetModules();
    browser("en-GB");
    const unset = await import("../i18n/locale");
    expect(unset.getDisplayPreference()).toBe("auto");
    expect(unset.getLocale()).toBe("en");
  });

  it("changing the display language makes no request and does not touch the response language", async () => {
    await mount();
    await choose("reply-language-default", "en");
    const before = [...state.calls];
    await choose("display-language", "en");
    expect(state.calls).toEqual(before);
    expect(session.responseLanguage).toBe("en");
  });
});
