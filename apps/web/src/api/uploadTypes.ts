import { message } from "../i18n/formatMessages";

/**
 * What the server stages (adapters/http/file_staging.py: MEDIA_TYPES, MAX_UPLOAD_BYTES). The screen keeps
 * a copy so the file dialog can filter and a wrong file is refused before it is sent; a server test
 * compares the two lists.
 */
export const UPLOAD_EXTENSIONS = [".hwpx", ".pdf", ".docx", ".xlsx", ".csv", ".json", ".md", ".txt", ".yaml", ".yml", ".html", ".htm"] as const;
export const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;
/** The value of the file input's accept attribute. */
export const UPLOAD_ACCEPT = UPLOAD_EXTENSIONS.join(",");
/** The same types as people name them, for sentences. */
export const UPLOAD_FORMATS = "HWPX · PDF · DOCX · XLSX · CSV · JSON · MD · TXT · YAML · HTML";
/** The response header in which the server names why it refused an upload. */
export const UPLOAD_ERROR_HEADER = "x-thoth-upload-error";

const REFUSALS = ["UPLOAD_UNSUPPORTED_TYPE", "UPLOAD_EMPTY", "UPLOAD_TOO_LARGE"] as const;
export type UploadRefusalCode = (typeof REFUSALS)[number];

export function isUploadRefusalCode(value: string | null): value is UploadRefusalCode {
  return REFUSALS.some(code => code === value);
}

/** A file that cannot be uploaded: which file and why, said in the screen language at the time it is shown. */
export class UploadRefused extends Error {
  constructor(readonly code: UploadRefusalCode, readonly filename: string) {
    super(code);
    Object.defineProperty(this, "message", { configurable: true, get: () => uploadRefusalText(code, filename) });
  }
}

export function uploadRefusalText(code: UploadRefusalCode, filename: string): string {
  if (code === "UPLOAD_UNSUPPORTED_TYPE") return message("uploadUnsupported", filename, UPLOAD_FORMATS);
  if (code === "UPLOAD_EMPTY") return message("uploadEmpty", filename);
  return message("uploadTooLarge", filename);
}

/** The refusal the server would give this file, known before anything is sent. */
export function refusalBeforeUpload(file: Pick<File, "name" | "size">): UploadRefused | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot === -1 ? "" : file.name.slice(dot).toLowerCase();
  if (!UPLOAD_EXTENSIONS.some(item => item === extension)) return new UploadRefused("UPLOAD_UNSUPPORTED_TYPE", file.name);
  if (file.size === 0) return new UploadRefused("UPLOAD_EMPTY", file.name);
  if (file.size > MAX_UPLOAD_BYTES) return new UploadRefused("UPLOAD_TOO_LARGE", file.name);
  return null;
}

