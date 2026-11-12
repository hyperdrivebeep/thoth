import { UiError } from "../i18n/UiError";
import { z } from "zod";
import { rpc } from "./rpcClient";
import { digestSchema, historicalResultSchema } from "./historySchemas";
import type { HistorySelection } from "./historyModels";

export type ResultSelection = Extract<HistorySelection, { kind: "result" }>;
const spanSchema = z.object({
  span_id: z.string(),
  project_id: z.string(),
  artifact_id: z.string(),
  source_version_id: z.string(),
  exact_text: z.string(),
  text_sha256: digestSchema,
  authority_state: z.string(),
  cutoff_state: z.string(),
  verification_state: z.string(),
  support_state: z.string(),
  locator: z
    .object({
      page: z.number().nullable().optional(),
      line: z.number().nullable().optional(),
      paragraph: z.number().nullable().optional(),
      sheet: z.string().nullable().optional(),
      cell_range: z.string().nullable().optional(),
      xml_path: z.string().nullable().optional(),
      uri: z.string().nullable().optional(),
    })
    .passthrough(),
});
const pageSchema = z.object({
  schema_version: z.literal("1.0.0"),
  result_revision_digest: digestSchema,
  actor_scope_digest: digestSchema,
  selected_count: z.number().int().nonnegative(),
  offset: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  next_offset: z.number().int().nonnegative().nullable(),
  items: z.array(
    z.object({
      span_id: z.string(),
      availability: z.enum(["AVAILABLE", "UNAVAILABLE", "BASIS_MISMATCH", "UNKNOWN_BASIS"]),
      reason_codes: z.array(z.string()),
      span: spanSchema.nullable(),
    }),
  ),
});
const responseSchema = historicalResultSchema.extend({
  selected_evidence: pageSchema.nullable().optional(),
});
const manifestSchema = z.object({
  operation_id: z.string(),
  request_ref: z.object({
    project_id: z.string(),
    entity_type: z.string(),
    entity_id: z.string(),
    revision_digest: digestSchema,
  }),
  source_refs: z.array(z.string()),
  source_basis: z.record(z.string()).optional(),
  result: z.record(z.unknown()),
});
export type ResultEvidencePage = z.infer<typeof pageSchema>;
export type EvidencePagePosition = { offset: number; resultDigest?: string };

// JSON object key ordering is not part of the stored answer's identity.
export function resultContentKey(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(resultContentKey).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.keys(value)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${resultContentKey((value as Record<string, unknown>)[key])}`,
      )
      .join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
/** The screen may show a summary view of the stored result: every key it shows must be in the stored result with the
 * same value, and a key it leaves out is taken as left out by the summary. A key the stored result does not have is not. */
function isShownPartOf(shown: Record<string, unknown>, stored: Record<string, unknown>): boolean {
  return Object.entries(shown).every(
    ([key, value]) =>
      Object.hasOwn(stored, key) && resultContentKey(value) === resultContentKey(stored[key]),
  );
}
function mismatch(): never {
  throw new UiError("evidence.binding");
}
function without(value: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> {
  return keys.length === 0
    ? value
    : Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key)));
}

function answerContent(
  value: Record<string, unknown>,
  thread: string,
  epoch: unknown,
  terminal: unknown,
) {
  // research_threads publication adds these four fields only to operation.result.
  // Validate that wrapper before comparing it with the immutable manifest.result.
  if (
    ("thread_id" in value && value.thread_id !== thread) ||
    ("request_epoch" in value && value.request_epoch !== epoch) ||
    ("contract_version" in value && value.contract_version !== 2) ||
    ("terminal_reason" in value && value.terminal_reason !== terminal)
  )
    mismatch();
  return Object.fromEntries(
    Object.entries(value).filter(
      ([key]) =>
        !["thread_id", "request_epoch", "contract_version", "terminal_reason"].includes(key),
    ),
  );
}

type Response = z.infer<typeof responseSchema>;
type Manifest = z.infer<typeof manifestSchema>;
/** The answer on the screen, the result the server returned and the immutable manifest must be the same stored answer. */
function bindAnswer(
  selection: ResultSelection,
  displayed: Record<string, unknown>,
  value: Response,
  manifest: Manifest,
  result: Record<string, unknown>,
) {
  const { scope } = selection;
  const [thread, epoch, terminal] = [
    scope.threadId,
    value.request.request_epoch,
    value.manifest?.terminal_reason,
  ];
  const displayedContent = answerContent(displayed, thread!, epoch, terminal);
  const resultBody = answerContent(result, thread!, epoch, terminal);
  const manifestBody = answerContent(manifest.result, thread!, epoch, terminal);
  // The server's read view of a result stored in an older form changes some top-level keys of the result (never the manifest); it
  // names them, and only those keys may differ from the manifest. Without that list (an older server) the two must be identical.
  const projected = value.projected_keys ?? [];
  // What the screen shows is the summary view of the stored answer. The server makes that same view of the result it returns;
  // it may only hold keys the result has. An older server sends none: the screen is then compared with the manifest itself.
  const shownBody = value.shown_result
    ? answerContent(value.shown_result, thread!, epoch, terminal)
    : manifestBody;
  if (value.shown_result && !Object.keys(shownBody).every((key) => Object.hasOwn(resultBody, key)))
    mismatch();
  // The latest round is shown from the summary view. A saved earlier round is shown from the stored result itself
  // (operation/result/read returns it whole). What the screen shows must be, all of it, a part of one of these forms
  // of the same stored answer; keys taken from two forms that disagree are refused.
  const shownIsStored = [shownBody, resultBody, manifestBody].some((body) =>
    isShownPartOf(displayedContent, body),
  );
  if (
    manifest.operation_id !== selection.operationId ||
    manifest.request_ref.project_id !== scope.projectId ||
    manifest.request_ref.entity_type !== "THREAD" ||
    manifest.request_ref.entity_id !== `request:${thread}` ||
    manifest.request_ref.revision_digest !== scope.requestDigest ||
    resultContentKey(without(resultBody, projected)) !==
      resultContentKey(without(manifestBody, projected)) ||
    !shownIsStored
  )
    mismatch();
}

async function read(
  selection: ResultSelection,
  displayed: Record<string, unknown>,
  digest: string | undefined,
  offset: number,
  expand: boolean,
  signal?: AbortSignal,
) {
  const { scope } = selection;
  if (!selection.operationId || !scope.projectId || !scope.threadId || !scope.requestDigest)
    mismatch();
  const response = await rpc<unknown>(
    "thread/result/read",
    {
      project_id: scope.projectId,
      thread_id: scope.threadId,
      request_revision_digest: scope.requestDigest,
      ...(digest ? { result_revision_digest: digest } : {}),
      contract_version: 2,
      ...(expand
        ? {
            include_selected_evidence: true,
            selected_evidence_offset: offset,
            selected_evidence_limit: 50,
          }
        : {}),
    },
    crypto.randomUUID(),
    signal,
  );
  if (response.state !== "SUCCEEDED") throw new UiError("evidence.queryIncomplete");
  const value = responseSchema.parse(response.value);
  if (
    value.project_id !== scope.projectId ||
    value.thread_id !== scope.threadId ||
    value.request_revision_digest !== scope.requestDigest ||
    value.operation_id !== selection.operationId ||
    (digest && value.result_revision_digest !== digest)
  )
    mismatch();
  if (value.availability === "UNAVAILABLE") throw new UiError("evidence.accessDenied");
  if (!value.manifest || !value.result_revision_digest)
    throw new UiError("evidence.versionUnknown");
  if (
    value.request.project_id !== scope.projectId ||
    value.request.thread_id !== scope.threadId ||
    value.request.operation_id !== selection.operationId ||
    !value.result
  )
    mismatch();
  const manifest = manifestSchema.parse(value.manifest);
  bindAnswer(selection, displayed, value, manifest, value.result);
  return { value, manifest };
}

export async function readResultEvidence(
  selection: ResultSelection,
  displayed: Record<string, unknown>,
  position: EvidencePagePosition,
  signal?: AbortSignal,
): Promise<ResultEvidencePage> {
  let digest = position.resultDigest ?? selection.resultDigest;
  if (selection.resultDigest && digest !== selection.resultDigest) mismatch();
  if (!digest)
    digest = (await read(selection, displayed, undefined, 0, false, signal)).value
      .result_revision_digest!;
  const { value, manifest } = await read(
    selection,
    displayed,
    digest,
    position.offset,
    true,
    signal,
  );
  const page = value.selected_evidence;
  if (!page) throw new UiError("evidence.unsupported");
  const expected = manifest.source_refs.slice(position.offset, position.offset + 50);
  const next =
    position.offset + expected.length < manifest.source_refs.length
      ? position.offset + expected.length
      : null;
  if (
    page.result_revision_digest !== digest ||
    page.offset !== position.offset ||
    page.limit !== 50 ||
    page.selected_count !== manifest.source_refs.length ||
    page.next_offset !== next ||
    page.items.length !== expected.length ||
    new Set(manifest.source_refs).size !== manifest.source_refs.length
  )
    mismatch();
  await Promise.all(
    page.items.map(async (item, index) => {
      if (item.span_id !== expected[index]) mismatch();
      if (item.availability !== "AVAILABLE") {
        if (item.span !== null) mismatch();
        return;
      }
      const span = item.span;
      if (
        !span ||
        span.project_id !== selection.scope.projectId ||
        span.span_id !== item.span_id ||
        manifest.source_basis?.[item.span_id] !== `${span.source_version_id}:${span.text_sha256}`
      )
        mismatch();
      const hash = Array.from(
        new Uint8Array(
          await crypto.subtle.digest("SHA-256", new TextEncoder().encode(span.exact_text)),
        ),
      )
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      if (hash !== span.text_sha256.toLowerCase()) mismatch();
    }),
  );
  if (signal?.aborted) throw signal.reason;
  return page;
}
