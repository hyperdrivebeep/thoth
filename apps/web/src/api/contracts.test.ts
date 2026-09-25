import { existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi, afterEach } from "vitest";
import { capabilities, capabilityCatalog, filterCapabilities, namespaces, parseInvocation, transportFor } from "./capabilities";
import { readQueries, rpc, RpcError } from "./rpcClient";
import { recordViews } from "./recordViews";
import { authorityOptions, authorityStates, cutoffOptions, cutoffStates, sourceConnectSchema } from "./sourcePolicy";
import { isThreadAnalysis } from "./research";
import { captureProjectCreation } from "./projectCreation";

// Full-stack contract integration: requires the repository's installed Python environment.
// Pure import/reflection only; never constructs a runtime, database, model, or connector.
const repository=fileURLToPath(new URL("../../../../",import.meta.url));
const python=[join(repository,".venv","Scripts","python.exe"),join(repository,".venv","bin","python")].find(existsSync);
if(!python)throw new Error("Web/backend contract integration requires the repository .venv Python environment.");
const backendContract=JSON.parse(execFileSync(python,["-c",[
  "import json",
  "from thoth.application.commands.sources import SourceConnectInput",
  "from thoth.application.commands.projects import ProjectCreateInput",
  "from thoth.protocol.bus import READ_QUERY_METHODS",
  "print(json.dumps({'source':SourceConnectInput.model_json_schema(),'project':ProjectCreateInput.model_json_schema(),'queries':sorted(READ_QUERY_METHODS)}))",
].join("\n")],{shell:false,encoding:"utf8",timeout:20000,cwd:repository,
  env:{...process.env,PYTHONPATH:join(repository,"src"),PYTHONDONTWRITEBYTECODE:"1"},
})) as {source:{$defs:Record<string,{enum?:string[]}>};project:{properties:{policy_binding_ref:{default:string}}};queries:string[]};

describe("canonical surface parity",()=>{
  it("covers every catalog method and namespace without inventing a completion metric",()=>{
    expect(new Set(capabilities.map(item=>item.name)).size).toBe(capabilityCatalog.runtime_method_count);
    expect(capabilities.filter(item=>item.canonical)).toHaveLength(capabilityCatalog.canonical_method_count);
    expect(capabilities.filter(item=>!item.canonical)).toHaveLength(capabilityCatalog.compatibility_alias_count);
    expect(filterCapabilities("")).toHaveLength(capabilities.length);
    expect(recordViews.map(item=>item.namespace).sort()).toEqual(namespaces);
    for(const view of recordViews){
      if(view.method) expect(capabilities.some(item=>item.name===view.method)).toBe(true);
      else expect(capabilities.filter(item=>item.namespace===view.namespace).every(item=>item.surface==="COMMAND")).toBe(true);
    }
  });
  it("uses the exact server query whitelist, not a method-name heuristic",()=>{
    expect([...readQueries].sort()).toEqual(backendContract.queries);
    expect(transportFor("thread/read")).toBe("/rpc/query");
    expect(transportFor("receipt/read")).toBe("/rpc");
    expect(transportFor("receipt/verify")).toBe("/rpc");
  });
  it("searches namespace labels, exact IDs and owners",()=>{
    expect(filterCapabilities("model/settings").map(item=>item.name)).toEqual(["model/settings/read","model/settings/update"]);
    expect(filterCapabilities("","field","QUERY")).toHaveLength(0);
    expect(filterCapabilities("가설").every(item=>item.namespace==="hypothesis")).toBe(true);
  });
  it("does not send arbitrary arrays or a different project scope",()=>{
    expect(()=>parseInvocation("[]","p")).toThrow("JSON 객체");
    expect(()=>parseInvocation('{"project_id":"q"}',"p")).toThrow("일치");
    expect(parseInvocation('{"project_id":"p","thread_id":"t"}',"p")).toEqual({project_id:"p",thread_id:"t"});
  });
});

describe("source intake payload contract",()=>{
  it("two projects use distinct IDs and inherit the server's scoped default policy",()=>{
    const first=captureProjectCreation("first","2026-09-14T10:00","general-rnd");
    const second=captureProjectCreation("second","2026-09-14T10:00","general-rnd");
    expect(first.input.project_id).not.toBe(second.input.project_id);
    expect(first.key).not.toBe(second.key);
    expect(first.input).not.toHaveProperty("policy_binding_ref");
    expect(second.input).not.toHaveProperty("policy_binding_ref");
    expect(backendContract.project.properties.policy_binding_ref.default).toBe("policy:default");
  });
  it("matches Python authority/cutoff enums and emits no UI-only enum aliases",()=>{
    expect([...authorityStates]).toEqual(backendContract.source.$defs.AuthorityState.enum);
    expect([...cutoffStates]).toEqual(backendContract.source.$defs.CutoffState.enum);
    expect(authorityOptions.map(item=>item.value).sort()).toEqual([...authorityStates].sort());
    expect(cutoffOptions.map(item=>item.value).sort()).toEqual([...cutoffStates].sort());
    const base={project_id:"p",relative_path:"source.md",media_type:"text/markdown",authority:"INFORMAL",cutoff_state:"AFTER_CUTOFF",security_class:"INTERNAL",resource_scope:{owner_kind:"PROJECT",visibility:"PROJECT_SHARED"},version_label:"web-sha"};
    expect(sourceConnectSchema.parse(base)).toEqual(base);
    expect(()=>sourceConnectSchema.parse({...base,authority:"REFERENCE"})).toThrow();
    expect(()=>sourceConnectSchema.parse({...base,cutoff_state:"FUTURE_EXCLUDED"})).toThrow();
    expect(()=>sourceConnectSchema.parse({...base,resource_scope:{owner_kind:"PROJECT",visibility:"WORKSTREAM"}})).toThrow();
  });
  it("keeps incomplete result contracts out of full-analysis consumers",()=>{
    expect(isThreadAnalysis({portfolio:{hypotheses:[]},action_plan:{alternatives:[]},selected_evidence_refs:[]})).toBe(false);
    expect(isThreadAnalysis({answer:"partial answer",gaps:["not yet checked"]})).toBe(false);
  });
});

afterEach(()=>vi.unstubAllGlobals());
it("routes authorized reads separately and retains typed error details",async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({jsonrpc:"2.0",id:"x",result:{operation_id:"",state:"SUCCEEDED",value:{}}})});
  vi.stubGlobal("fetch",fetch);
  await rpc("thread/list",{project_id:"p"},"same-key");
  expect(fetch.mock.calls[0][0]).toBe("/rpc/query");
  await rpc("receipt/read",{project_id:"p",receipt_id:"r"},"read-key");
  expect(fetch.mock.calls[1][0]).toBe("/rpc");
  fetch.mockResolvedValue({ok:true,json:async()=>({jsonrpc:"2.0",id:"x",error:{code:-32003,message:"denied",data:{reason_code:"POLICY_BLOCKED"}}})});
  await expect(rpc("action/create",{project_id:"p"},"command-key")).rejects.toMatchObject({name:"RpcError",details:{reason_code:"POLICY_BLOCKED"}} satisfies Partial<RpcError>);
});
