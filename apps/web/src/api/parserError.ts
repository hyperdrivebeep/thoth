import { t } from "../i18n/locale";
import { messages, type MessageKey } from "../i18n/messages";

const PREFIX = "parserError.";
const UNKNOWN_KEY = "parserError.unknown" satisfies MessageKey;

/** The parser reason codes the screen has a sentence for. */
export const PARSER_ERROR_CODES: readonly string[] = Object.keys(messages)
  .filter((key) => key.startsWith(PREFIX) && key !== UNKNOWN_KEY)
  .map((key) => key.slice(PREFIX.length));

export type ParserFailureText = { code: string; text: string };

/** The server puts the reason in the error's data as parser_error. A code with no sentence reads as
 *  the general sentence; the code itself stays available for the technical details. */
export function parserFailureText(error: unknown): ParserFailureText | null {
  const details = (error as { details?: unknown } | null)?.details;
  const code =
    details && typeof details === "object"
      ? (details as Record<string, unknown>).parser_error
      : undefined;
  if (typeof code !== "string" || code === "") return null;
  return {
    code,
    text: t(PARSER_ERROR_CODES.includes(code) ? ((PREFIX + code) as MessageKey) : UNKNOWN_KEY),
  };
}
