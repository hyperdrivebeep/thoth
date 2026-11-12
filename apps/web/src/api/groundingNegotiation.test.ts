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

it("negotiates all three reads across old, v5 and legacy servers without changing commands", async () => {
  const sent: { method: string; params: { input: Record<string, unknown> } }[] = [];
  const reads = ["thread/read", "operation/result/read", "thread/result/read"];
  let ready: Record<string, unknown> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      const request = JSON.parse(String(options.body));
      sent.push(request);
      return Response.json({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          operation_id: "",
          state: "SUCCEEDED",
          value: request.method === "workspace/ready" ? ready : {},
        },
      });
    }),
  );
  await rpc("workspace/ready", {}, "ready-unsupported");
  for (const method of reads) {
    await rpc(method, { project_id: "p", operation_id: "op" }, `unsupported-${method}`);
    expect(sent.at(-1)!.params.input).not.toHaveProperty("expected_result_contract");
  }
  ready = { expected_result_contracts: ["v4", "v5"] };
  await rpc("workspace/ready", {}, "ready-v5");
  for (const method of reads) {
    await rpc(method, { project_id: "p" }, method);
    expect(sent.at(-1)!.params.input.expected_result_contract).toBe("v5");
  }
  await rpc("thread/read", { expected_result_contract: "v4" }, "explicit-v4");
  expect(sent.at(-1)!.params.input.expected_result_contract).toBe("v4");
  const command = {
    project_id: "p",
    thread_id: "t",
    contract_version: 2,
    instruction: "Synthetic question",
    response_language: "en",
  };
  const originalCommand = structuredClone(command);
  await rpc("thread/input", command, "command");
  expect(sent.at(-1)!.params.input).toEqual(originalCommand);
  expect(command).toEqual(originalCommand);
  ready = { response_language_scope: "REQUEST" };
  await rpc("workspace/ready", {}, "ready-legacy");
  for (const method of reads) {
    await rpc(method, { project_id: "p", operation_id: "op" }, `legacy-${method}`);
    expect(sent.at(-1)!.params.input.expected_result_contract).toBe("v4");
  }
  ready = {};
  await rpc("workspace/ready", {}, "ready-old");
  for (const method of reads) {
    await rpc(method, { project_id: "p", operation_id: "op" }, `old-${method}`);
    expect(sent.at(-1)!.params.input).not.toHaveProperty("expected_result_contract");
  }
});
