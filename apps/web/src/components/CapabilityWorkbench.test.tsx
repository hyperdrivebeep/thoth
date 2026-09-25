// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { CapabilityWorkbench } from "./CapabilityWorkbench";
import { DomainRecords } from "./DomainRecords";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { visibleRecordViews } from "../api/recordViews";

const fixture=vi.hoisted(()=>({calls:[] as {method:string;input:Record<string,unknown>}[],defer:false,resolve:null as null|(()=>void)}));
vi.mock("../api/files",()=>({stageFile:async()=>({relative_path:"web/fixture.md",media_type:"text/markdown",byte_sha256:"a".repeat(64),bytes:20,filename:"fixture.md"})}));
vi.mock("../api/rpcClient",async(importOriginal)=>({...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc:async(method:string,input:Record<string,unknown>)=>{
    fixture.calls.push({method,input});
    if(fixture.defer)await new Promise<void>(resolve=>{fixture.resolve=resolve;});
    return {state:"SUCCEEDED",operation_id:"",value:{scope_result:`${input.project_id}-only-result`}};
  },
}));
let root:Root;let container:HTMLDivElement;let client:QueryClient;
const tick=()=>new Promise(resolve=>setTimeout(resolve,15));
async function render(node:ReactNode){await act(async()=>{root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);await tick();});}
async function mount(node:ReactNode){Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});fixture.calls=[];fixture.defer=false;fixture.resolve=null;container=document.createElement("div");document.body.append(container);root=createRoot(container);client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});await render(node);}
async function click(label:string){const button=Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(item=>item.textContent?.includes(label));expect(button).toBeTruthy();await act(async()=>{button!.click();await tick();});}
afterEach(async()=>{if(root)await act(async()=>root.unmount());client?.clear();container?.remove();});

it("a malformed panel leaves navigation available and resets on a different view",async()=>{
  const logger=vi.spyOn(console,"error").mockImplementation(()=>{});
  function BrokenPanel():ReactNode{throw new Error("TEST_MALFORMED_RESPONSE");}
  try{
    await mount(<><nav>프로젝트 탐색 유지</nav><WorkspaceErrorBoundary resetKey="research"><BrokenPanel/></WorkspaceErrorBoundary></>);
    expect(container.textContent).toContain("프로젝트 탐색 유지");
    expect(container.textContent).toContain("이 작업 화면을 표시하지 못했습니다");
    await render(<><nav>프로젝트 탐색 유지</nav><WorkspaceErrorBoundary resetKey="records"><p>기록 조회 가능</p></WorkspaceErrorBoundary></>);
    expect(container.textContent).toContain("기록 조회 가능");
  }finally{logger.mockRestore();}
});

it("catalog exploration is inert and command execution requires the exact-input acknowledgement",async()=>{
  await mount(<CapabilityWorkbench projectId="p" initialNamespace="model"/>);
  expect(fixture.calls).toHaveLength(0);
  await click("model/settings/update");
  expect(fixture.calls).toHaveLength(0);
  const run=Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(item=>item.textContent?.includes("확인한 명령 실행"))!;
  expect(run.disabled).toBe(true);
  await act(async()=>{container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();});
  expect(run.disabled).toBe(false);
  await click("확인한 명령 실행");
  expect(fixture.calls).toEqual([{method:"model/settings/update",input:{project_id:"p"}}]);
});

it("does not expose ProjectPack or field fixtures in the local API workbench", async () => {
  await mount(<CapabilityWorkbench projectId="p" />);
  const methods = [...container.querySelectorAll(".method-item strong")].map(item => item.textContent ?? "");
  expect(methods.some(method => method.startsWith("projectpack/"))).toBe(false);
  expect(methods.some(method => method.startsWith("field/"))).toBe(false);
  const namespaces = [...container.querySelectorAll<HTMLSelectElement>('[aria-label="기능 namespace"] option')].map(option => option.value);
  expect(namespaces).not.toContain("projectpack");
  expect(namespaces).not.toContain("field");
});

it("late capability response is not relabeled with the next project's context",async()=>{
  await mount(<CapabilityWorkbench projectId="p" initialNamespace="model"/>);
  fixture.defer=true;await click("이 조회 실행");
  await render(<CapabilityWorkbench projectId="q" initialNamespace="model"/>);
  await act(async()=>{fixture.resolve!();await tick();});
  expect(container.textContent).not.toContain("p-only-result");
  expect(container.querySelector<HTMLTextAreaElement>("#rpc-input")!.value).toContain('"q"');
});

it("keeps fixture and command-only field namespaces out of the visible record browser",async()=>{
  await mount(<DomainRecords projectId="p" threadId="t" onOpenCatalog={()=>{}}/>);
  expect(container.querySelectorAll("button.domain-card")).toHaveLength(visibleRecordViews.length);
  expect(fixture.calls).toHaveLength(0);
  expect(container.textContent).not.toContain("검증 예제");
  expect(container.textContent).not.toContain("현장 평가");
  expect(fixture.calls).toHaveLength(0);
  await click("프로젝트 기억");await click("기록 불러오기");
  expect(fixture.calls).toEqual([{method:"memory/list",input:{project_id:"p"}}]);
});

it("late curated record responses cannot replace another project or namespace",async()=>{
  await mount(<DomainRecords projectId="p" threadId="t" onOpenCatalog={()=>{}}/>);
  fixture.defer=true;await click("기록 불러오기");
  await render(<DomainRecords projectId="q" threadId="t" onOpenCatalog={()=>{}}/>);
  await act(async()=>{fixture.resolve!();await tick();});
  expect(container.textContent).not.toContain("p-only-result");
  expect(container.textContent).toContain("조회 전");
});

it("successful file connect resets the native chooser and permits selecting the same file again",async()=>{
  await mount(<FileConnectionPanel projectId="p"/>);
  const input=container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const file=new File(["fixture source"],"fixture.md",{type:"text/markdown"});
  let nativeValue="C:\\fakepath\\fixture.md";
  Object.defineProperty(input,"value",{configurable:true,get:()=>nativeValue,set:(value:string)=>{nativeValue=value;}});
  Object.defineProperty(input,"files",{configurable:true,value:[file]});
  await act(async()=>{
    input.dispatchEvent(new Event("change",{bubbles:true}));
    const visibility=container.querySelector<HTMLSelectElement>('[aria-label="자료 사용 범위"]')!;
    visibility.value="PROJECT_SHARED";visibility.dispatchEvent(new Event("change",{bubbles:true}));
    const authority=container.querySelector<HTMLSelectElement>('[aria-label="자료 성격"]')!;
    authority.value="INFORMAL";authority.dispatchEvent(new Event("change",{bubbles:true}));
  });
  await click("자료 연결");expect(nativeValue).toBe("");
  expect(container.querySelector('[aria-label="기준시점 상태"]')).toBeNull();
  expect(fixture.calls[0].input.cutoff_state).toBe("ELIGIBLE");
  expect(fixture.calls[0].input.authority).toBe("INFORMAL");
  await act(async()=>{input.dispatchEvent(new Event("change",{bubbles:true}));});
  await click("자료 연결");expect(fixture.calls.filter(call=>call.method==="project/source/connect")).toHaveLength(2);
});
