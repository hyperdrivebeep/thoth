// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

const fixture = vi.hoisted(() => ({ calls: [] as {method: string; input: Record<string, unknown>; key:string}[],
  effort: "low", pending: null as null | (() => void), delay: false, failOnce:false, unavailable:false,
  reason:"MODEL_CAPABILITY_UNKNOWN", saved:{provider:"retired",model:"old-model",reasoning_effort:"high"}, inheritThread:false, projectSettingsDenied:false, threadDenied:false }));
vi.mock("../api/rpcClient", async (importOriginal) => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()), cancelRpcOperation: vi.fn(),
  rpc: async (method: string, input: Record<string, unknown>, key:string) => {
    fixture.calls.push({method, input,key});
    let value: unknown = {};
    if (method === "project/list") value = {projects: ["p", "q"].map(project_id =>
      ({project_id, name: project_id, overlay: "test", lifecycle: "ACTIVE"}))};
    if (method === "model/settings/update") { fixture.effort = (input.selection as {reasoning_effort: string}).reasoning_effort; fixture.unavailable = false; }
    if (method === "model/settings/read" && !input.thread_id && fixture.inheritThread && fixture.projectSettingsDenied) throw new Error("PROJECT_SETTINGS_SCOPE_REQUIRED");
    if (method === "model/settings/read" && input.thread_id && fixture.threadDenied) throw new Error("AUTHORIZATION_DENIED");
    if (method.startsWith("model/settings/")) value = {settings_digest: fixture.unavailable && !(input.thread_id && fixture.inheritThread) ? "saved-digest" : null,
      selection: fixture.unavailable ? input.thread_id && fixture.inheritThread ? {provider: null, model: null, reasoning_effort: null} : fixture.saved : {provider: null, model: null, reasoning_effort: null},
      effective_settings: fixture.unavailable ? null : {provider: "test", model: "test", reasoning_effort: fixture.effort},
      availability: fixture.unavailable ? "UNAVAILABLE" : "AVAILABLE", reason_code: fixture.unavailable ? fixture.reason : null,
      model_options: [{provider: "test", model: "test", reasoning_efforts: ["low", "high"], default_effort: "low"}]};
    if (method === "project/source/list") value = {artifacts: []};
    if (method === "evidence/list") value = {evidence: []};
    if (method === "thread/list") value = {threads:["t","other"].map(thread_id=>({project_id:input.project_id,thread_id,problem:thread_id,execution_state:"IDLE",lifecycle:"OPEN"}))};
    if (method === "project/create") {
      if(fixture.failOnce){fixture.failOnce=false;throw new Error("project response lost");}
      if(fixture.delay)await new Promise<void>(resolve=>{fixture.pending=resolve;});
      value={project_id:input.project_id,name:input.name,overlay:input.overlay,lifecycle:"DRAFT"};
    }
    if (method === "thread/start" || method === "thread/input") {
      if (fixture.failOnce) {fixture.failOnce=false;throw new Error("transport response lost");}
      if (fixture.delay) await new Promise<void>(resolve => {fixture.pending = resolve;});
      value = {thread_id: "t", operation_id: "op", request_epoch: 1};
    }
    if (method === "thread/read") value = {project_id: input.project_id, thread_id: input.thread_id, operation_state: "RUNNING", current_result: null};
    return {value, state: "SUCCEEDED", operation_id: "op"};
  },
}));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const tick = () => new Promise(resolve => setTimeout(resolve, 25));
async function flush() { await act(async () => {await tick();}); }
function effort() { return container.querySelector<HTMLSelectElement>('[aria-label="연구 추론강도"]')!; }
async function select(value: string) { await act(async () => {effort().value = value; effort().dispatchEvent(new Event("change", {bubbles: true}));}); }
async function submit() {
  await act(async () => {
    const input = container.querySelector<HTMLTextAreaElement>("#live-problem")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "question");
    input.dispatchEvent(new Event("input", {bubbles: true}));
  });
  await act(async () => {container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));});
  await flush();
}
async function mount(unavailable = false, options: { reason?: string; saved?: { provider: string; model: string; reasoning_effort: string }; inheritThread?: boolean } = {}) {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
  fixture.calls = []; fixture.effort = "low"; fixture.delay = false; fixture.pending = null;fixture.failOnce=false;fixture.unavailable=unavailable;
  fixture.reason=options.reason??"MODEL_CAPABILITY_UNKNOWN";fixture.saved=options.saved??{provider:"retired",model:"old-model",reasoning_effort:"high"};fixture.inheritThread=options.inheritThread??false;fixture.projectSettingsDenied=false;fixture.threadDenied=false;
  sessionStorage.clear();
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container); client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  await act(async () => {root.render(<QueryClientProvider client={client}><LiveProjectWorkspace /></QueryClientProvider>);});
  await flush();
  await act(async () => {container.querySelector<HTMLButtonElement>("button.project-button")!.click();});
  await flush();
  if (!unavailable) expect(effort().value).toBe("low");
}
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); container?.remove(); });

it("one-shot high returns to low visibly and in the next same-thread request", async () => {
  await mount(); await submit(); // established Thread: same child key thereafter
  await select("high"); await submit();
  expect(fixture.calls.filter(c => c.method === "thread/input").at(-1)?.input.reasoning_effort).toBe("high");
  expect(effort().value).toBe("low");
  await submit();
  expect(fixture.calls.filter(c => c.method === "thread/input").at(-1)?.input.reasoning_effort).toBeUndefined();
});

it("keeps an unavailable saved model and effort visible until a user selects a new option", async () => {
  await mount(true);
  expect(container.textContent).toContain("저장된 선택: retired / old-model · 추론강도 high");
  expect(container.textContent).toContain("현재 모델 목록에서 저장된 모델을 찾지 못했습니다");
  const model = container.querySelector<HTMLSelectElement>('[aria-label="연구 모델"]')!;
  expect(model.value).toBe("");
  const save = container.querySelector<HTMLButtonElement>("button.model-defaults")!;
  expect(save.disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
  await act(async () => { model.value = "test/test"; model.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(save.disabled).toBe(true);
  await select("high");
  expect(save.disabled).toBe(false);
  await act(async () => save.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/settings/update" &&
    (call.input.selection as { provider: string; model: string }).provider === "test" &&
    (call.input.selection as { provider: string; model: string }).model === "test" &&
    call.input.expected_digest === "saved-digest")).toBe(true);
});

it("does not silently replace an unsupported saved effort", async () => {
  await mount(true, { reason: "MODEL_REASONING_EFFORT_UNSUPPORTED", saved: { provider: "test", model: "test", reasoning_effort: "ultra" } });
  expect(container.textContent).toContain("저장된 선택: test / test · 추론강도 ultra");
  const save = container.querySelector<HTMLButtonElement>("button.model-defaults")!;
  expect(save.disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
  await select("high");
  expect(save.disabled).toBe(false);
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("describes an empty thread selection as project inheritance without writing a replacement", async () => {
  await mount(true, { inheritThread: true });
  await act(async () => {
    const selector = container.querySelector<HTMLSelectElement>('[aria-label="현재 작업 선택"]')!;
    selector.value = "t"; selector.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  expect(container.textContent).toContain("이 작업은 프로젝트 모델 설정을 상속하며 별도 선택으로 대체하지 않았습니다");
  expect(container.textContent).toContain("상속한 프로젝트 선택: retired / old-model · 추론강도 high");
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("hides inherited project model details when the separate project read is denied", async () => {
  await mount(true, { inheritThread: true });
  fixture.projectSettingsDenied = true;
  await act(async () => {
    const selector = container.querySelector<HTMLSelectElement>('[aria-label="현재 작업 선택"]')!;
    selector.value = "t"; selector.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush(); await flush();
  expect(container.textContent).toContain("이 작업은 프로젝트 모델 설정을 상속하며 별도 선택으로 대체하지 않았습니다");
  expect(container.textContent).toContain("상속한 프로젝트 모델을 읽지 못했습니다");
  expect(container.textContent).not.toContain("상속한 프로젝트 선택: retired / old-model");
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
});

it("removes cached inherited details when the actor loses thread settings access", async () => {
  await mount(true, { inheritThread: true });
  await act(async () => {
    const selector = container.querySelector<HTMLSelectElement>('[aria-label="현재 작업 선택"]')!;
    selector.value = "t"; selector.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush(); await flush();
  expect(container.textContent).toContain("상속한 프로젝트 선택: retired / old-model");
  fixture.threadDenied = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["model-settings", "p", "t"], exact: true }); });
  await flush();
  expect(container.textContent).not.toContain("상속한 프로젝트 선택: retired / old-model");
  expect(container.textContent).toContain("AUTHORIZATION_DENIED");
});

it("rechecks and hides cached parent details after project scope is revoked", async () => {
  await mount(true, { inheritThread: true });
  await act(async () => {
    const selector = container.querySelector<HTMLSelectElement>('[aria-label="현재 작업 선택"]')!;
    selector.value = "t"; selector.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush(); await flush();
  expect(container.textContent).toContain("상속한 프로젝트 선택: retired / old-model");
  fixture.projectSettingsDenied = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["model-settings", "p", "t"], exact: true }); });
  await flush(); await flush();
  expect(container.textContent).toContain("이 작업은 프로젝트 모델 설정을 상속하며 별도 선택으로 대체하지 않았습니다");
  expect(container.textContent).toContain("상속한 프로젝트 모델을 읽지 못했습니다");
  expect(container.textContent).not.toContain("상속한 프로젝트 선택: retired / old-model");
  expect(container.querySelector<HTMLSelectElement>('[aria-label="연구 모델"]')?.value).toBe("");
  expect(container.querySelector<HTMLSelectElement>('[aria-label="연구 추론강도"]')?.value).toBe("");
  expect(container.querySelector<HTMLButtonElement>("button.model-defaults")?.disabled).toBe(true);
  const model = container.querySelector<HTMLSelectElement>('[aria-label="연구 모델"]')!;
  await act(async () => { model.value = "test/test"; model.dispatchEvent(new Event("change", { bubbles: true })); });
  const save = container.querySelector<HTMLButtonElement>("button.model-defaults")!;
  expect(effort().value).toBe("");
  expect(save.disabled).toBe(true);
  await select("high");
  expect(save.disabled).toBe(false);
  expect(fixture.calls.some(call => call.method === "model/settings/update")).toBe(false);
  await act(async () => save.click()); await flush();
  expect(fixture.calls.some(call => call.method === "model/settings/update" && call.input.expected_digest === null &&
    (call.input.selection as { reasoning_effort: string }).reasoning_effort === "high")).toBe(true);
});

it("a new draft survives a late admission", async () => {
  await mount(); await submit();
  fixture.delay = true; await select("high"); await submit();
  await select("low");
  await act(async () => {fixture.pending!(); await tick();});
  fixture.delay = false;
  await submit();
  expect(fixture.calls.filter(c => c.method === "thread/input").at(-1)?.input.reasoning_effort).toBe("low");
});

it("saved defaults survive reload and apply to successive requests", async () => {
  await mount(); await select("high");
  await act(async () => {
    const save = Array.from(container.querySelectorAll<HTMLButtonElement>("fieldset button"))[0];
    save.click();
  });
  await flush();
  expect(fixture.effort).toBe("high");
  await act(async () => { await client.invalidateQueries({queryKey: ["model-settings"]}); });
  await submit(); await submit();
  expect(effort().value).toBe("high");
  expect(fixture.calls.filter(c => c.method === "thread/start" || c.method === "thread/input").every(c =>
    (c.input.reasoning_effort ?? fixture.effort) === "high")).toBe(true);
});

it("a late admission cannot replace a different project draft", async () => {
  await mount(); fixture.delay = true; await select("high"); await submit();
  await act(async () => {container.querySelectorAll<HTMLButtonElement>("button.project-button")[1].click();});
  await flush(); await select("low");
  await act(async () => {fixture.pending!(); await tick();});
  expect(effort().value).toBe("low");
  fixture.delay = false; await submit();
  const last = fixture.calls.filter(c => c.method === "thread/start").at(-1)!;
  expect(last.input.project_id).toBe("q");
  expect(last.input.reasoning_effort).toBe("low");
});

it("same-draft transport retry reuses the idempotency key and double-submit is blocked",async()=>{
  await mount();fixture.failOnce=true;await submit();
  const failed=fixture.calls.filter(c=>c.method==="thread/start").at(-1)!;
  fixture.delay=true;
  await act(async()=>{
    const form=container.querySelector("form.prompt-composer")!;
    form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
    form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
  });
  expect(fixture.calls.filter(c=>c.method==="thread/start")).toHaveLength(2);
  expect(fixture.calls.filter(c=>c.method==="thread/start").at(-1)!.key).toBe(failed.key);
  await act(async()=>{fixture.pending!();await tick();});
});

it("a late admission cannot replace a different selected thread",async()=>{
  await mount();await submit();fixture.delay=true;await select("high");await submit();
  await act(async()=>{
    const selector=container.querySelector<HTMLSelectElement>('[aria-label="현재 작업 선택"]')!;
    selector.value="other";selector.dispatchEvent(new Event("change",{bubbles:true}));
  });
  await flush();await select("low");
  await act(async()=>{fixture.pending!();await tick();});
  fixture.delay=false;await submit();
  const last=fixture.calls.filter(c=>c.method==="thread/input").at(-1)!;
  expect(last.input.thread_id).toBe("other");expect(last.input.reasoning_effort).toBe("low");
});

it("ordinary navigation only automatically calls the actual nonmutating query surface",async()=>{
  await mount();
  expect(fixture.calls.every(call=>["project/list","thread/list","project/source/list","evidence/list","model/settings/read","workspace/ready"].includes(call.method))).toBe(true);
  await act(async()=>{container.querySelector<HTMLElement>('[role="tab"][data-tab-id="records"]')?.click();});
  expect(fixture.calls.some(call=>call.method==="receipt/verify")).toBe(false);
});

it("project-create retry preserves project ID and key without a globally shared policy binding",async()=>{
  await mount();
  await act(async()=>{container.querySelector<HTMLButtonElement>("button.new-project-button")!.click();});
  await act(async()=>{Array.from(container.querySelectorAll<HTMLButtonElement>(".project-home-list button")).find(button=>button.textContent==="새 프로젝트")!.click();});
  await act(async()=>{
    const input=container.querySelector<HTMLInputElement>('input[placeholder="연구할 문제 또는 프로젝트 이름"]')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(input,"second project");
    input.dispatchEvent(new Event("input",{bubbles:true}));
  });
  fixture.failOnce=true;
  const form=container.querySelector("form.onboarding-card")!;
  await act(async()=>{form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));await tick();});
  fixture.delay=true;
  await act(async()=>{form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));});
  const calls=fixture.calls.filter(call=>call.method==="project/create");
  expect(calls).toHaveLength(2);expect(calls[0].key).toBe(calls[1].key);
  expect(calls[0].input.project_id).toBe(calls[1].input.project_id);
  expect(calls[0].input).not.toHaveProperty("policy_binding_ref");
  await act(async()=>{fixture.pending!();await tick();});
});
