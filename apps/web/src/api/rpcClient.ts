import { jsonRpcResponseSchema } from "./protocol";
import { expectedResultContract } from "./expectedResultContract";
import { t } from "../i18n/locale";

export type RpcResult<T> = {
  operation_id: string;
  state: string;
  value: T;
};
export const readQueries = new Set(["thread/read", "thread/list", "thread/activity/list", "model/settings/read",
  "model/localAsset/read", "project/retrieval/experiment/read",
  "model/credential/list", "model/credential/login/status", "workspace/setup/read", "workspace/ready",
  "project/read", "project/list", "project/source/list", "evidence/list", "operation/read",
  "operation/result/read", "operation/checkpoint/read",
  "revision/timeline/read", "revision/timeline/item/read", "thread/result/read",
  "thread/result/compare/read", "project/review/list",
  "revision/diff/read", "revision/restore/preview", "revision/read", "revision/content/read",
  "revision/head/read", "revision/history/read", "trace/read", "trace/export", "trace/importPreview", "trace/history", "hypothesis/link/list", "hypothesis/test/result/list", "hypothesis/same/list", "trace/closure/list", "trace/lesson/list"]);

const INTERNAL_ERROR = -32603;
/** What the server answers for any unexpected failure. The screen says it in the person's language. */
const INTERNAL_FAILURE_TEXT = "internal command failure";

function shownMessage(message: string, code: number): string {
  return code === INTERNAL_ERROR && message === INTERNAL_FAILURE_TEXT ? t("rpc.internalFailure") : message;
}

/** The server logs an unexpected failure under its operation id; showing the id lets a report name it. */
function internalFailureRef(code: number, details: Record<string, unknown> | undefined): string {
  const operationId = details?.operation_id;
  if (code !== INTERNAL_ERROR || typeof operationId !== "string" || !operationId) return "";
  return ` (${operationId})`;
}

export class RpcError extends Error {
  constructor(message: string, readonly code: number, readonly details: Record<string, unknown>) {
    super(`${shownMessage(message, code)} [${code}]${internalFailureRef(code, details)}`);
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

const typedResultMethods = new Set(["thread/read", "operation/result/read", "thread/result/read"]);
let resultContract: "v4" | "v5" | undefined;

export async function rpc<T>(
  method: string,
  input: Record<string, unknown>,
  idempotencyKey: string,
  signal?: AbortSignal,
  options: { timeoutMs?: number } = {},
): Promise<RpcResult<T>> {
  const bounded = timeoutSignal(signal, options.timeoutMs ?? 15000);
  if (method === "workspace/ready") resultContract = undefined;
  const readInput = resultContract && typedResultMethods.has(method) && !("expected_result_contract" in input)
    ? { ...input, expected_result_contract: resultContract } : input;
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
        params: { _meta: { idempotencyKey }, input: readInput },
      }),
    }), signal, bounded.signal);
    if (!response.ok) {
      throw new RpcTransportError(`RPC transport failed (${response.status})`, "HTTP", response.status);
    }
    const envelope = jsonRpcResponseSchema.parse(await transportRead(() => response.json(), signal, bounded.signal));
    if (envelope.error) {
      throw new RpcError(envelope.error.message, envelope.error.code, envelope.error.data);
    }
    if (method === "workspace/ready") {
      const value = envelope.result?.value;
      resultContract = expectedResultContract(value);
    }
    return envelope.result as RpcResult<T>;
  } finally {
    bounded.dispose();
  }
}
