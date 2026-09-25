import { afterEach, expect, it, vi } from "vitest";
import { rpc, RpcError, RpcTransportError } from "./rpcClient";

afterEach(() => vi.unstubAllGlobals());
const read = (signal?: AbortSignal) => rpc("workspace/ready", {}, "read-transport-test", signal);

it.each([
  [new TypeError("Failed to fetch"), "NETWORK"],
  [new DOMException("timed out", "TimeoutError"), "TIMEOUT"],
] as const)("types failures at the network boundary", async (failure, kind) => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
  await expect(read()).rejects.toMatchObject({ name: "RpcTransportError", kind, retryable: true });
});

it.each([[429, true], [504, true], [403, false], [401, false]] as const)("classifies HTTP %i without parsing its message", async (status, retryable) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status })));
  await expect(read()).rejects.toMatchObject({ name: "RpcTransportError", kind: "HTTP", status, retryable });
});

it("does not turn response corruption into a retryable transport failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 200 })));
  await expect(read()).rejects.not.toBeInstanceOf(RpcTransportError);
});

it("retains an application access denial as RpcError", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ jsonrpc: "2.0", id: "read", error: {
    code: -32040, message: "denied", data: { reason_code: "RESOURCE_SCOPE_ACCESS_DENIED" },
  }})));
  await expect(read()).rejects.toBeInstanceOf(RpcError);
});

it("does not reclassify the caller's cancellation as a network failure", async () => {
  const controller = new AbortController();
  const cancellation = new DOMException("caller stopped reading", "AbortError");
  controller.abort(cancellation);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cancellation));
  await expect(read(controller.signal)).rejects.toBe(cancellation);
});

it.each(["thread/result/compare/read", "project/review/list"] as const)("uses the read endpoint for %s", async (method) => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ jsonrpc: "2.0", id: "read", result: { operation_id: "op", state: "SUCCEEDED", value: {} } }));
  vi.stubGlobal("fetch", fetch);
  await rpc(method, { project_id: "project:p" }, "followup-read-route");
  expect(fetch).toHaveBeenCalledWith("/rpc/query", expect.objectContaining({ method: "POST" }));
});
