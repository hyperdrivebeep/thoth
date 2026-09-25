import { jsonRpcResponseSchema } from "./protocol";

export type RpcResult<T> = {
  operation_id: string;
  state: string;
  value: T;
};
export const readQueries = new Set(["thread/read", "thread/list", "thread/activity/list", "model/settings/read",
  "model/credential/list", "workspace/setup/read", "workspace/ready",
  "project/read", "project/list", "project/source/list", "evidence/list", "operation/read",
  "operation/result/read", "operation/checkpoint/read",
  "revision/timeline/read", "revision/timeline/item/read", "thread/result/read",
  "thread/result/compare/read", "project/review/list",
  "revision/diff/read", "revision/restore/preview", "revision/read", "revision/content/read",
  "revision/head/read", "revision/history/read"]);

export class RpcError extends Error {
  constructor(message: string, readonly code: number, readonly details: Record<string, unknown>) {
    super(`${message} [${code}]`);
    this.name = "RpcError";
  }
}

export class RpcTransportError extends Error {
  constructor(message: string, readonly kind: "NETWORK" | "TIMEOUT" | "HTTP", readonly status: number | null = null) {
    super(message);
    this.name = "RpcTransportError";
  }
  get retryable() { return this.kind !== "HTTP" || this.status === 429 || (this.status !== null && this.status >= 500); }
}

async function transportRead<T>(read: () => Promise<T>, caller: AbortSignal | undefined, bounded: AbortSignal): Promise<T> {
  try { return await read(); }
  catch (error) {
    if (caller?.aborted) throw error;
    if (bounded.aborted || (error instanceof DOMException && error.name === "TimeoutError")) {
      throw new RpcTransportError("RPC timed out", "TIMEOUT");
    }
    if (error instanceof TypeError) throw new RpcTransportError(error.message, "NETWORK");
    throw error;
  }
}

function timeoutSignal(signal: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(new DOMException("RPC timed out", "TimeoutError")), timeoutMs);
  const abort = () => controller.abort(signal?.reason);
  const dispose = () => {
    globalThis.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  };
  controller.signal.addEventListener("abort", dispose, { once: true });
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  return { signal: controller.signal, dispose };
}

export async function rpc<T>(
  method: string,
  input: Record<string, unknown>,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<RpcResult<T>> {
  const bounded = timeoutSignal(signal, 15000);
  try {
    const response = await transportRead(() => fetch(readQueries.has(method) ? "/rpc/query" : "/rpc", {
      method: "POST",
      signal: bounded.signal,
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: crypto.randomUUID(),
        method,
        params: { _meta: { idempotencyKey }, input },
      }),
    }), signal, bounded.signal);
    if (!response.ok) {
      throw new RpcTransportError(`RPC transport failed (${response.status})`, "HTTP", response.status);
    }
    const envelope = jsonRpcResponseSchema.parse(await transportRead(() => response.json(), signal, bounded.signal));
    if (envelope.error) {
      throw new RpcError(envelope.error.message, envelope.error.code, envelope.error.data);
    }
    return envelope.result as RpcResult<T>;
  } finally {
    bounded.dispose();
  }
}

type OperationResult<T> = {
  operation_id: string;
  state: string;
  result: T | null;
  error: { code?: number; message?: string } | null;
};

export async function rpcAsync<T>(
  method: string,
  input: Record<string, unknown>,
  idempotencyKey: string,
  options: {
    onStarted?: (operationId: string) => void;
    pollMs?: number;
  } = {},
): Promise<RpcResult<T>> {
  const request = {
    jsonrpc: "2.0",
    id: crypto.randomUUID(),
    method,
    params: { _meta: { idempotencyKey }, input },
  };
  const startedResponse = await fetch("/rpc/async", {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!startedResponse.ok) throw new Error(`비동기 RPC 시작 실패 (${startedResponse.status})`);
  const started = jsonRpcResponseSchema.parse(await startedResponse.json());
  if (started.error) throw new Error(`${started.error.message} [${started.error.code}]`);
  const operationId = String(started.result?.operation_id ?? "");
  const projectId = String(input.project_id ?? "");
  if (!operationId || !projectId) throw new Error("비동기 operation 식별자가 없습니다.");
  options.onStarted?.(operationId);
  const interval = options.pollMs ?? 900;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, interval));
    const statusResponse = await fetch(
      `/operations/${encodeURIComponent(operationId)}?project_id=${encodeURIComponent(projectId)}`,
      { credentials: "same-origin" },
    );
    if (!statusResponse.ok) throw new Error(`operation 조회 실패 (${statusResponse.status})`);
    const operation = (await statusResponse.json()) as OperationResult<T>;
    if (operation.state === "SUCCEEDED" && operation.result !== null) {
      return { operation_id: operationId, state: operation.state, value: operation.result };
    }
    if (operation.state === "FAILED") {
      throw new Error(operation.error?.message ?? "분석 operation이 실패했습니다.");
    }
    if (operation.state === "CANCELLED") {
      throw new Error("사용자가 분석을 취소했습니다.");
    }
  }
}

export async function cancelRpcOperation(projectId: string, operationId: string) {
  const response = await fetch(`/operations/${encodeURIComponent(operationId)}/cancel`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!response.ok) throw new Error(`operation 취소 실패 (${response.status})`);
  const envelope = jsonRpcResponseSchema.parse(await response.json());
  if (envelope.error) throw new RpcError(envelope.error.message, envelope.error.code, envelope.error.data);
  return envelope;
}
