import { jsonRpcResponseSchema } from "./protocol";
import { rpc, type RpcResult } from "./rpcClient";
import { z } from "zod";

/**
 * Local retrieval preparation (S4): typed read contracts plus a bounded long-operation
 * wrapper. Reads go through the shared `rpc()`; long actions reuse the existing
 * `/rpc/async`, `/operations/{id}` and `/operations/{id}/cancel` routes with their own
 * time limits (headers and body together, each request capped by what is left of the
 * whole wait), so a client-side timeout is reported as "result unconfirmed", never as
 * "cancelled". The shared `rpcClient` is not changed.
 */

export const WORKSPACE_PROJECT_ID = "system:workspace";

const assetReadSchema = z.object({
  schema_version: z.literal("1.0.0"), supported: z.boolean(), can_manage: z.boolean(),
  model_name: z.string(), manifest_digest: z.string(), total_bytes: z.number().nonnegative(),
  asset_state: z.enum(["MODEL_REQUIRED", "READY", "FAILED"]), dependency_available: z.boolean(),
  worker_loaded: z.boolean(), reason_code: z.string().nullable(),
  active: z.object({ operation_id: z.string(), state: z.enum(["COPYING", "DOWNLOADING", "VERIFYING"]),
    transferred_bytes: z.number().nonnegative(), total_bytes: z.number().nonnegative() }).nullable(),
});
const projectReadSchema = z.object({
  project_id: z.string(), can_prepare: z.boolean(), enabled: z.boolean(), generation: z.number().int().nonnegative(),
  state: z.enum(["OFF", "NO_SOURCES", "INDEX_PENDING", "INDEXING", "READY", "FAILED", "CANCEL_REQUESTED"]),
  ready_sources: z.number().nonnegative(), total_sources: z.number().nonnegative(),
  pending_sources: z.number().nonnegative(), failed_sources: z.number().nonnegative(),
  jobs: z.array(z.object({ job_id: z.string(), generation: z.number().int(),
    state: z.enum(["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED", "STALE"]), reason_code: z.string().nullable() })),
  active_operation_id: z.string().nullable(),
  // True while sources are indexed only under an earlier preprocessing version.
  outdated_index: z.boolean().optional(),
});

// Runtime validation owns the DTO contract; TypeScript follows the same schema.
export type LocalAssetRead = z.infer<typeof assetReadSchema>;
export type RetrievalExperimentRead = z.infer<typeof projectReadSchema>;
export type RetrievalJob = RetrievalExperimentRead["jobs"][number];

export async function readLocalAsset(signal?: AbortSignal): Promise<RpcResult<LocalAssetRead>> {
  const result = await rpc<unknown>("model/localAsset/read", { project_id: WORKSPACE_PROJECT_ID }, crypto.randomUUID(), signal);
  return { ...result, value: assetReadSchema.parse(result.value) };
}

export async function readRetrievalExperiment(projectId: string, signal?: AbortSignal): Promise<RpcResult<RetrievalExperimentRead>> {
  const result = await rpc<unknown>("project/retrieval/experiment/read", { project_id: projectId }, crypto.randomUUID(), signal);
  const value = projectReadSchema.parse(result.value);
  if (value.project_id !== projectId) throw new Error("LOCAL_RETRIEVAL_SCOPE_MISMATCH");
  return { ...result, value };
}

/** Compare-and-swap on the project's generation; the server answers with the latest project read. */
export async function configureRetrievalExperiment(
  input: { project_id: string; enabled: boolean; expected_generation: number },
  idempotencyKey: string,
): Promise<RpcResult<RetrievalExperimentRead>> {
  const result = await rpc<unknown>("project/retrieval/experiment/configure", input, idempotencyKey);
  const value = projectReadSchema.parse(result.value);
  if (value.project_id !== input.project_id) throw new Error("LOCAL_RETRIEVAL_SCOPE_MISMATCH");
  return { ...result, value };
}

export type LocalRetrievalLongMethod =
  | "model/localAsset/import"
  | "model/localAsset/install"
  | "project/retrieval/experiment/prepare"
  | "project/embeddingIndex/retry";

export type OperationTimeouts = {
  /** One `/rpc/async` start request. */
  startMs: number;
  /** One `/operations/{id}` read. */
  pollMs: number;
  /** Pause between two reads. */
  pollIntervalMs: number;
  /** Whole wait for a result, measured from the start of the call. */
  totalMs: number;
  /** One cancel request. */
  cancelMs: number;
};

export const DEFAULT_OPERATION_TIMEOUTS: OperationTimeouts = {
  startMs: 15_000, pollMs: 15_000, pollIntervalMs: 900, totalMs: 610_000, cancelMs: 15_000,
};

/** Every way a long action can end, as the client can actually tell it apart. */
export type OperationOutcome =
  | { kind: "SUCCEEDED"; operationId: string; value: unknown }
  | { kind: "FAILED"; operationId: string; reasonCode: string | null }
  /** The server confirmed that the work stopped. */
  | { kind: "CANCELLED"; operationId: string }
  /** The server explicitly refused to start (a 4xx other than 408/429, or a JSON-RPC error); nothing is running. */
  | { kind: "REJECTED"; reasonCode: string | null; httpStatus: number | null }
  /** The client stopped waiting; the server may still be working (or may have started). */
  | { kind: "UNCONFIRMED"; operationId: string | null }
  /** The caller aborted the wait (screen changed); the server was not told to stop. */
  | { kind: "ABANDONED"; operationId: string | null };

type OperationRecord = {
  operation_id?: unknown;
  state?: unknown;
  result?: unknown;
  error?: { code?: unknown; message?: unknown; data?: unknown } | null;
};

type BoundedJson =
  /** Headers and the whole JSON body arrived within the limit; `body` is undefined when it was not JSON. */
  | { ok: true; status: number; httpOk: boolean; body: unknown }
  | { ok: false; cause: "TIMEOUT" | "NETWORK" | "ABORTED" };

/** One request whose headers *and* body must arrive within `timeoutMs`; a stalled body is a timeout too. */
async function boundedJson(url: string, init: RequestInit, signal: AbortSignal | undefined, timeoutMs: number): Promise<BoundedJson> {
  if (signal?.aborted) return { ok: false, cause: "ABORTED" };
  const controller = new AbortController();
  let timedOut = false;
  const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    const response = await fetch(url, { ...init, signal: controller.signal, credentials: "same-origin" });
    let body: unknown;
    try { body = await response.json(); }
    catch (error) {
      if (controller.signal.aborted) throw error;
      body = undefined;
    }
    return { ok: true, status: response.status, httpOk: response.ok, body };
  } catch {
    if (signal?.aborted) return { ok: false, cause: "ABORTED" };
    return { ok: false, cause: timedOut ? "TIMEOUT" : "NETWORK" };
  } finally {
    globalThis.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

/** An HTTP status that leaves it open whether the server took the request (it may be working on it). */
const startUnconfirmedStatus = (status: number) => status === 408 || status === 429 || status >= 500;

function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise(resolve => {
    if (signal?.aborted) { resolve(); return; }
    const timer = globalThis.setTimeout(() => { signal?.removeEventListener("abort", done); resolve(); }, ms);
    const done = () => { globalThis.clearTimeout(timer); resolve(); };
    signal?.addEventListener("abort", done, { once: true });
  });
}

const codeShape = /^[A-Z][A-Z0-9_]{2,79}$/;

/** Only a code-shaped value is kept; free text (which may carry paths) is dropped. */
export function reasonCodeOf(error: { message?: unknown; data?: unknown } | null | undefined): string | null {
  if (!error) return null;
  const data = error.data;
  if (data && typeof data === "object" && "reason_code" in data) {
    const code = (data as { reason_code?: unknown }).reason_code;
    if (typeof code === "string" && codeShape.test(code)) return code;
  }
  if (typeof error.message === "string" && codeShape.test(error.message)) return error.message;
  return null;
}

export type RunLocalOperationRequest = {
  method: LocalRetrievalLongMethod;
  input: Record<string, unknown> & { project_id: string };
  /** Reused on a retry of the same action, so the server can answer with the same work. */
  idempotencyKey: string;
  /** When known, no new start request is sent; only the result of this work is read. */
  operationId?: string | null;
  signal?: AbortSignal;
  onStarted?: (operationId: string) => void;
  timeouts?: Partial<OperationTimeouts>;
};

export async function runLocalOperation(request: RunLocalOperationRequest): Promise<OperationOutcome> {
  const timeouts = { ...DEFAULT_OPERATION_TIMEOUTS, ...request.timeouts };
  const deadline = Date.now() + timeouts.totalMs;
  /** Each request gets its own limit, never more than what is left of the whole wait. */
  const remaining = (limit: number) => Math.max(1, Math.min(limit, deadline - Date.now()));
  const { signal } = request;
  const projectId = request.input.project_id;
  let operationId = request.operationId ?? null;

  if (!operationId) {
    if (signal?.aborted) return { kind: "ABANDONED", operationId: null };
    const started = await boundedJson("/rpc/async", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: crypto.randomUUID(),
        method: request.method,
        params: { _meta: { idempotencyKey: request.idempotencyKey }, input: request.input },
      }),
    }, signal, remaining(timeouts.startMs));
    if (!started.ok) {
      if (started.cause === "ABORTED") return { kind: "ABANDONED", operationId: null };
      return { kind: "UNCONFIRMED", operationId: null };
    }
    const parsed = jsonRpcResponseSchema.safeParse(started.body);
    if (!started.httpOk) {
      // 408/429/5xx do not prove that nothing started: keep the same key for a later check.
      if (startUnconfirmedStatus(started.status)) return { kind: "UNCONFIRMED", operationId: null };
      return { kind: "REJECTED", reasonCode: parsed.success ? reasonCodeOf(parsed.data.error) : null, httpStatus: started.status };
    }
    if (!parsed.success) return { kind: "UNCONFIRMED", operationId: null };
    if (parsed.data.error) return { kind: "REJECTED", reasonCode: reasonCodeOf(parsed.data.error), httpStatus: null };
    const id = parsed.data.result?.operation_id;
    if (typeof id !== "string" || !id) return { kind: "UNCONFIRMED", operationId: null };
    operationId = id;
    request.onStarted?.(operationId);
  }

  for (;;) {
    if (signal?.aborted) return { kind: "ABANDONED", operationId };
    if (Date.now() >= deadline) return { kind: "UNCONFIRMED", operationId };
    const read = await boundedJson(
      `/operations/${encodeURIComponent(operationId)}?project_id=${encodeURIComponent(projectId)}`,
      { method: "GET" }, signal, remaining(timeouts.pollMs),
    );
    if (!read.ok) {
      if (read.cause === "ABORTED") return { kind: "ABANDONED", operationId };
      return { kind: "UNCONFIRMED", operationId };
    }
    if (!read.httpOk) return { kind: "UNCONFIRMED", operationId };
    const record = read.body as OperationRecord | undefined;
    if (!record || typeof record !== "object") return { kind: "UNCONFIRMED", operationId };
    if (record.state === "SUCCEEDED" && record.result !== null && record.result !== undefined) {
      return { kind: "SUCCEEDED", operationId, value: record.result };
    }
    if (record.state === "FAILED") return { kind: "FAILED", operationId, reasonCode: reasonCodeOf(record.error) };
    if (record.state === "CANCELLED") return { kind: "CANCELLED", operationId };
    await sleep(timeouts.pollIntervalMs, signal);
  }
}

export type CancelRequestResult =
  /** The server accepted the request; whether the work stopped is known only from its later state. */
  | { kind: "REQUESTED" }
  | { kind: "REJECTED"; reasonCode: string | null; httpStatus: number | null }
  | { kind: "UNCONFIRMED" };

/** Asks the server to cancel. This never claims that the work stopped. */
export async function cancelLocalOperation(
  projectId: string, operationId: string, signal?: AbortSignal, timeoutMs: number = DEFAULT_OPERATION_TIMEOUTS.cancelMs,
): Promise<CancelRequestResult> {
  const sent = await boundedJson(`/operations/${encodeURIComponent(operationId)}/cancel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  }, signal, timeoutMs);
  if (!sent.ok) return { kind: "UNCONFIRMED" };
  const parsed = jsonRpcResponseSchema.safeParse(sent.body);
  if (!sent.httpOk) {
    if (startUnconfirmedStatus(sent.status)) return { kind: "UNCONFIRMED" };
    return { kind: "REJECTED", reasonCode: parsed.success ? reasonCodeOf(parsed.data.error) : null, httpStatus: sent.status };
  }
  if (!parsed.success) return { kind: "UNCONFIRMED" };
  if (parsed.data.error) return { kind: "REJECTED", reasonCode: reasonCodeOf(parsed.data.error), httpStatus: null };
  return { kind: "REQUESTED" };
}
