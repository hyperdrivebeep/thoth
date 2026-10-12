import { createContext, useContext } from "react";
import type { ResponseLanguage } from "../api/localWorkspacePersistence";

/** Metadata of the displayed saved result, never the next request or current UI preference. */
export const ResponseLanguageContext = createContext<ResponseLanguage | null>(null);
export function useResultLanguage() { return useContext(ResponseLanguageContext); }
export function resultLanguage(result: Record<string, unknown> | null | undefined): ResponseLanguage | null {
  return result?.response_language === "en" || result?.response_language === "ko" ? result.response_language : null;
}
