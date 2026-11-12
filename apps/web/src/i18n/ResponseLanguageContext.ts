import { createContext, useContext } from "react";
import { isResponseLanguage, type ResponseLanguage } from "../api/responseLanguages";

/** Metadata of the displayed saved result, never the next request or current UI preference. */
export const ResponseLanguageContext = createContext<ResponseLanguage | null>(null);
export function useResultLanguage() {
  return useContext(ResponseLanguageContext);
}
export function resultLanguage(
  result: Record<string, unknown> | null | undefined,
): ResponseLanguage | null {
  return isResponseLanguage(result?.response_language) ? result.response_language : null;
}
