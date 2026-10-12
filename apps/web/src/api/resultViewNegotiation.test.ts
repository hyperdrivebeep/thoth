import { afterEach, expect, it, vi } from "vitest";
import { rpc } from "./rpcClient";

afterEach(() => vi.restoreAllMocks());
it("opts result reads into v4 only after capability discovery and never changes command input", async () => {
  const inputs: {method:string;params:{input:Record<string,unknown>}}[] = [];
  let supported = false;
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    const input = JSON.parse(String(options.body)); inputs.push(input);
    const value = input.method === "workspace/ready" && supported ? {response_language_scope:"REQUEST"} : {};
    return new Response(JSON.stringify({jsonrpc:"2.0",id:input.id,result:{operation_id:"read",state:"SUCCEEDED",value}}), {status:200});
  }));
  try {
    await rpc("workspace/ready", {}, "ready-old");
    await rpc("operation/result/read", {project_id:"p",operation_id:"op"}, "read-old");
    expect(inputs.at(-1)!.params.input).not.toHaveProperty("expected_result_contract");
    supported = true;
    await rpc("workspace/ready", {}, "ready-new");
    await rpc("operation/result/read", {project_id:"p",operation_id:"op"}, "read-new");
    expect(inputs.at(-1)!.params.input.expected_result_contract).toBe("v4");
    const command = {project_id:"p",thread_id:"t",contract_version:2,instruction:"question",response_language:"en"};
    await rpc("thread/input", command, "new-input");
    expect(inputs.at(-1)!.params.input).toEqual(command);
    supported = false;
    await rpc("workspace/ready", {}, "ready-old-again");
    await rpc("thread/result/read", {project_id:"p"}, "read-old-again");
    expect(inputs.at(-1)!.params.input).not.toHaveProperty("expected_result_contract");
  } finally { vi.unstubAllGlobals(); }
});
