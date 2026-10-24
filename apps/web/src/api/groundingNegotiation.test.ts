import { afterEach, expect, it, vi } from "vitest";
import { expectedResultContract } from "./expectedResultContract";
import { rpc } from "./rpcClient";

afterEach(() => vi.unstubAllGlobals());

it.each([
  [{ expected_result_contracts: ["v4", "v5"] }, "v5"],
  [{ expected_result_contracts: ["v4"] }, "v4"],
  [{ response_language_scope: "REQUEST" }, "v4"],
  [{ expected_result_contracts: [], response_language_scope: "REQUEST" }, undefined],
  [{}, undefined],
])("negotiates declared versions with a conservative legacy fallback", (ready, expected) => {
  expect(expectedResultContract(ready)).toBe(expected);
});

it("uses v5 for all three reads, preserves explicit choice and commands, and drops stale capability", async () => {
  const sent: {method: string; params: {input: Record<string, unknown>}}[] = [];
  let ready: Record<string, unknown> = { expected_result_contracts: ["v4", "v5"] };
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    const request = JSON.parse(String(options.body)); sent.push(request);
    return Response.json({ jsonrpc: "2.0", id: request.id, result: { operation_id: "", state: "SUCCEEDED", value: request.method === "workspace/ready" ? ready : {} } });
  }));
  await rpc("workspace/ready", {}, "ready-v5");
  for (const method of ["thread/read", "operation/result/read", "thread/result/read"]) {
    await rpc(method, {project_id: "p"}, method);
    expect(sent.at(-1)!.params.input.expected_result_contract).toBe("v5");
  }
  await rpc("thread/read", {expected_result_contract: "v4"}, "explicit-v4");
  expect(sent.at(-1)!.params.input.expected_result_contract).toBe("v4");
  const command = {project_id: "p", instruction: "Synthetic question", response_language: "en"};
  await rpc("thread/input", command, "command");
  expect(sent.at(-1)!.params.input).toEqual(command);
  ready = {response_language_scope: "REQUEST"};
  await rpc("workspace/ready", {}, "ready-legacy");
  await rpc("thread/read", {}, "read-legacy");
  expect(sent.at(-1)!.params.input.expected_result_contract).toBe("v4");
  ready = {};
  await rpc("workspace/ready", {}, "ready-old");
  await rpc("thread/read", {}, "read-old");
  expect(sent.at(-1)!.params.input).not.toHaveProperty("expected_result_contract");
});
