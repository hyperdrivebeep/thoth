import { message as formatMessage } from "../i18n/formatMessages";
import { UiError } from "../i18n/UiError";
export type StagedFile = {
  relative_path: string;
  filename: string;
  media_type: string;
  byte_sha256: string;
  bytes: number;
};

export type UploadProgress = {
  phase: "uploading" | "processing";
  loaded: number;
  total: number | null;
};
export type StageFileOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: UploadProgress) => void;
};

const STALL_MS = 120_000;

export function stageFile(file: File, projectId: string, options: StageFileOptions = {}): Promise<StagedFile> {
  if (options.signal?.aborted) return Promise.reject(new UiError("sources.fileUploadCancelled"));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let settled = false;
    let processing = false;
    let loaded = 0;
    let total: number | null = file.size;
    const finish = (error?: unknown, result?: StagedFile) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", cancel);
      xhr.onload = xhr.onerror = xhr.onabort = xhr.ontimeout = null;
      xhr.upload.onprogress = xhr.upload.onload = null;
      if (error !== undefined) reject(error);
      else resolve(result!);
    };
    const stop = (error: Error) => { if (!settled) { finish(error); xhr.abort(); } };
    const cancel = () => stop(new UiError("sources.fileUploadCancelled"));
    const renew = () => {
      clearTimeout(timer);
      timer = setTimeout(() => stop(new UiError("sources.fileUploadStalled")), STALL_MS);
    };
    xhr.upload.onprogress = event => {
      if (settled || processing || event.loaded <= loaded) return;
      loaded = event.loaded;
      total = event.lengthComputable && event.total > 0 ? event.total : null;
      renew();
      options.onProgress?.({ phase: "uploading", loaded, total });
    };
    xhr.upload.onload = () => {
      if (settled || processing) return;
      processing = true;
      clearTimeout(timer);
      timer = undefined;
      options.onProgress?.({ phase: "processing", loaded, total });
    };
    xhr.onload = () => {
      if (settled) return;
      if (xhr.status < 200 || xhr.status >= 300) {
        let detail: { detail?: string } | null = null;
        try { detail = JSON.parse(xhr.responseText); } catch { /* Preserve the HTTP fallback. */ }
        finish(new Error(detail?.detail ?? formatMessage("filePrepareFailed", xhr.status)));
        return;
      }
      try { finish(undefined, JSON.parse(xhr.responseText) as StagedFile); }
      catch (error) { finish(error); }
    };
    xhr.onerror = () => finish(new Error(formatMessage("filePrepareFailed", xhr.status)));
    xhr.onabort = () => finish(new UiError("sources.fileUploadCancelled"));
    xhr.ontimeout = () => stop(new UiError("sources.fileUploadStalled"));
    options.signal?.addEventListener("abort", cancel, { once: true });
    try {
      xhr.open("POST", "/files/stage");
      xhr.setRequestHeader("x-thoth-project-id", projectId);
      const body = new FormData();
      body.append("file", file);
      renew();
      options.onProgress?.({ phase: "uploading", loaded, total });
      if (!settled) xhr.send(body);
    } catch (error) { finish(error); }
  });
}
