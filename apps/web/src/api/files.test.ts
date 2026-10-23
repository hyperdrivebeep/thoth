// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { stageFile, type StagedFile } from "./files";
import { setLocale } from "../i18n/locale";

type Progress = { phase: "uploading" | "processing"; loaded: number; total: number | null };
type Options = { signal?: AbortSignal; onProgress?: (progress: Progress) => void };
const upload: (file: File, project: string, options?: Options) => Promise<StagedFile> = stageFile;
const staged: StagedFile = { relative_path: "uploads/p/a.md", filename: "a.md", media_type: "text/markdown", byte_sha256: "a".repeat(64), bytes: 4 };
const stalled = "No data was sent for 2 minutes, so the upload stopped. Check your connection and try again. No source was connected.";

class FakeXHR {
  static instances: FakeXHR[] = [];
  upload = { onprogress: null as null | ((event: ProgressEvent) => void), onload: null as null | (() => void) };
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;
  onabort: null | (() => void) = null;
  ontimeout: null | (() => void) = null;
  status = 0;
  responseText = "";
  timeout = 0;
  body: FormData | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn((body: FormData) => { this.body = body; });
  abort = vi.fn(() => this.onabort?.());
  constructor() { FakeXHR.instances.push(this); }
  progress(loaded: number, total = 100, lengthComputable = true) {
    this.upload.onprogress?.(Object.assign(new Event("progress"), { loaded, total, lengthComputable }));
  }
  complete(status = 200, text = JSON.stringify(staged)) {
    this.status = status; this.responseText = text; this.onload?.();
  }
}

beforeEach(() => {
  vi.useFakeTimers(); setLocale("en"); FakeXHR.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setLocale("ko"); });

function start(options?: Options) {
  const result: { value?: StagedFile; error?: Error } = {};
  const file = new File(["data"], "a.md", { type: "text/markdown" });
  const done = upload(file, "project:p", options).then(
    value => { result.value = value; }, error => { result.error = error; },
  );
  return { result, done, file };
}
function xhr() { expect(FakeXHR.instances).toHaveLength(1); return FakeXHR.instances[0]; }
async function expectNoUploadTimer() {
  // Drain DOM bookkeeping at the same instant; a leaked 120-second timer would remain.
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
}

it("fails at 120 seconds without progress, not at 119.999 seconds", async () => {
  const { result } = start();
  await vi.advanceTimersByTimeAsync(119999);
  expect(result.error).toBeUndefined();
  await vi.advanceTimersByTimeAsync(1);
  expect(result.error?.message).toBe(stalled);
  expect(xhr().abort).toHaveBeenCalledTimes(1);
  await expectNoUploadTimer();
  setLocale("ko");
  expect(result.error?.message).toBe("파일을 보내는 중 2분 동안 진행이 없어 멈췄습니다. 연결을 확인하고 다시 시도하세요. 연결된 자료는 없습니다.");
});

it("allows a slow upload beyond two minutes while bytes keep increasing", async () => {
  const updates: Progress[] = [];
  const { result, done, file } = start({ onProgress: value => updates.push(value) });
  const request = xhr();
  expect(request.open).toHaveBeenCalledWith("POST", "/files/stage");
  expect(request.setRequestHeader).toHaveBeenCalledWith("x-thoth-project-id", "project:p");
  expect(request.body?.get("file")).toBe(file);
  for (const loaded of [10, 20, 30]) {
    await vi.advanceTimersByTimeAsync(119000); request.progress(loaded);
    expect(result.error).toBeUndefined();
  }
  request.upload.onload?.(); request.complete(); await done;
  expect(result.value).toEqual(staged);
  expect(updates).toContainEqual({ phase: "uploading", loaded: 30, total: 100 });
  expect(updates.at(-1)?.phase).toBe("processing");
  expect(request.timeout).toBe(0);
  await expectNoUploadTimer();
});

it("does not renew the deadline for repeated or decreasing byte counts", async () => {
  const { result } = start(); const request = xhr();
  await vi.advanceTimersByTimeAsync(110000); request.progress(10);
  await vi.advanceTimersByTimeAsync(119000); request.progress(10); request.progress(9);
  expect(result.error).toBeUndefined();
  await vi.advanceTimersByTimeAsync(1000);
  expect(result.error?.message).toBe(stalled);
});

it("tracks increasing bytes even if the total length is unknown", async () => {
  const progress = vi.fn(); const { result, done } = start({ onProgress: progress });
  const request = xhr();
  await vi.advanceTimersByTimeAsync(119000); request.progress(10, 0, false);
  await vi.advanceTimersByTimeAsync(119000);
  expect(result.error).toBeUndefined();
  expect(progress).toHaveBeenLastCalledWith({ phase: "uploading", loaded: 10, total: null });
  request.complete(); await done;
});

it("has no processing deadline after upload completion and ignores late progress", async () => {
  const { result, done } = start(); const request = xhr();
  await vi.advanceTimersByTimeAsync(119000); request.upload.onload?.();
  await vi.advanceTimersByTimeAsync(180000); request.progress(100);
  expect(result.error).toBeUndefined();
  await expectNoUploadTimer();
  request.complete(); await done;
  expect(result.value).toEqual(staged);
});

it("allows cancellation while the server is processing without a timer", async () => {
  const controller = new AbortController();
  const { result, done } = start({ signal: controller.signal }); const request = xhr();
  request.upload.onload?.(); await vi.advanceTimersByTimeAsync(180000);
  expect(result.error).toBeUndefined();
  controller.abort(); await done;
  expect(result.error?.message).toBe("Upload cancelled. No source was connected.");
  expect(request.abort).toHaveBeenCalledTimes(1);
  await expectNoUploadTimer();
});

it("cancels the request and ignores a late success callback", async () => {
  const controller = new AbortController(); const { result, done } = start({ signal: controller.signal });
  const request = xhr(); const lateLoad = request.onload;
  controller.abort(); await done;
  expect(request.abort).toHaveBeenCalledTimes(1);
  expect(result.error?.message).toBe("Upload cancelled. No source was connected.");
  request.status = 200; request.responseText = JSON.stringify(staged); lateLoad?.();
  expect(result.value).toBeUndefined();
  await expectNoUploadTimer();
});

it("does not send a request for an already cancelled signal", async () => {
  const controller = new AbortController(); controller.abort(); const { result } = start({ signal: controller.signal });
  await vi.advanceTimersByTimeAsync(0);
  expect(result.error?.message).toBe("Upload cancelled. No source was connected.");
  expect(FakeXHR.instances).toHaveLength(0);
  expect(fetch).not.toHaveBeenCalled();
  await expectNoUploadTimer();
});

it("does not send if cancellation occurs during initial progress notification", async () => {
  const controller = new AbortController();
  const { result, done } = start({ signal: controller.signal, onProgress: () => controller.abort() });
  await done;
  expect(result.error?.message).toBe("Upload cancelled. No source was connected.");
  expect(xhr().send).not.toHaveBeenCalled();
  await expectNoUploadTimer();
});

it.each([[413, '{"detail":"server detail"}', "server detail"], [503, "not json", "503"]])(
  "preserves HTTP error handling for %s", async (status, text, expected) => {
    const { result, done } = start(); xhr().complete(Number(status), String(text)); await done;
    expect(result.error?.message).toContain(String(expected));
    await expectNoUploadTimer();
  },
);

it("cleans up after malformed successful JSON", async () => {
  const { result, done } = start(); xhr().complete(200, "invalid json"); await done;
  expect(result.error).toBeInstanceOf(SyntaxError);
  await expectNoUploadTimer();
});

it("cleans up after network errors", async () => {
  const { result, done } = start(); xhr().onerror?.(); await done;
  expect(result.error).toBeInstanceOf(Error);
  await expectNoUploadTimer();
});
