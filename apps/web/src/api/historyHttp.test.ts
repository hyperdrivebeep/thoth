import { readFileSync } from "node:fs";
import { afterEach, expect, it, vi } from "vitest";
import { readHistoryEntry, readHistoryPage } from "./history";
import { compareHistory } from "./historyComparison";
import { readRestorePreview } from "./restore";
import { rpc } from "./rpcClient";

const origin = process.env.THOTH_HISTORY_HTTP_ORIGIN;
const descriptorPath = process.env.THOTH_HISTORY_HTTP_DESCRIPTOR;
afterEach(() => vi.unstubAllGlobals());

it.runIf(Boolean(origin && descriptorPath))("reads exact records, diff and blocked apply preview from the isolated Python HTTP server", async () => {
  const target = new URL(origin!);
  if (!["127.0.0.1", "localhost", "[::1]"].includes(target.hostname)) throw new Error("This contract check only accepts an explicitly selected loopback fixture.");
  const descriptor = JSON.parse(readFileSync(descriptorPath!, "utf8"));
  if (descriptor.model !== "CONTROLLED_TEST_DOUBLE" || descriptor.apply_ready !== false) throw new Error("Expected the read-only controlled fixture descriptor.");
  const calls: string[] = [];
  const nativeFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url) !== "/rpc/query") throw new Error("HTTP verification forbids journaling and mutation transports.");
    calls.push(JSON.parse(String(init?.body)).method);
    return nativeFetch(new URL(String(url), target), init);
  });
  const scope = { projectId: descriptor.project_id as string, threadId: descriptor.thread_id as string };
  const before = await rpc<Record<string, unknown>>("thread/read", { project_id: scope.projectId, thread_id: scope.threadId }, crypto.randomUUID());
  let page = await readHistoryPage(scope, null);
  let item = page.items.find(row => row.selection.kind === "record" && row.selection.record.digest === descriptor.selection.target_revision_digest);
  for (let index = 0; !item && page.nextCursor && index < 3; index += 1) {
    page = await readHistoryPage(scope, page.nextCursor);
    item = page.items.find(row => row.selection.kind === "record" && row.selection.record.digest === descriptor.selection.target_revision_digest);
  }
  if (!item) throw new Error("The fixture's historical hypothesis is not present in the authorized timeline pages.");
  const entry = await readHistoryEntry(item.selection);
  expect(entry.restoreSelection?.expectedHead).toBe(descriptor.selection.expected_current_head);
  expect(entry.capability.previewSupported).toBe(true);
  expect(entry.content).not.toBeNull();
  const comparison = await compareHistory(entry.restoreSelection!);
  expect(comparison.changes.some(change => change.path.includes("statement") && change.before !== change.after)).toBe(true);
  const preview = await readRestorePreview(entry.restoreSelection!);
  expect(preview.principalScope).toBe(page.actorScope);
  expect(preview.applyReady).toBe(false);
  expect(preview.selection.targetDigest).toBe(descriptor.selection.target_revision_digest);
  const after = await rpc<Record<string, unknown>>("thread/read", { project_id: scope.projectId, thread_id: scope.threadId }, crypto.randomUUID());
  expect(Array.isArray(before.value.model_dispatches)).toBe(true);
  expect(after.value.model_dispatches).toEqual(before.value.model_dispatches);
  expect(calls).not.toContain("revision/restore/apply");
  expect(calls).not.toContain("thread/input");
}, 30000);
