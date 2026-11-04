import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { setLocale, t } from "../i18n/locale";
import type { MessageKey } from "../i18n/messages";
import { PARSER_ERROR_CODES, parserFailureText } from "./parserError";

const RAW_CODE = /[A-Z]{3,}_[A-Z_]{3,}/;
/** The values of ParserErrorCode, read from the server source. The public schemas under
 *  schemas/protocol do not list them, so this file is the one place the web test can read them from. */
function serverCodes(): string[] {
  const source = readFileSync(new URL("../../../../src/thoth/domain/enums.py", import.meta.url), "utf8");
  const block = /class ParserErrorCode\(StrEnum\):\r?\n([\s\S]*?)\r?\n\r?\n/.exec(source)?.[1] ?? "";
  return [...block.matchAll(/^\s+[A-Z_]+ = "([A-Z_]+)"/gm)].map(match => match[1]);
}
const failure = (code: unknown) => ({ details: { parser_error: code }, message: "source parsing failed [-32001]" });

afterEach(() => setLocale("ko"));

describe("parser error sentences", () => {
  it("has a sentence for exactly the codes the server defines, so a new server code fails here", () => {
    const server = serverCodes();
    expect(server.length).toBeGreaterThan(15);
    expect([...PARSER_ERROR_CODES].sort()).toEqual([...server].sort());
  });

  it.each(["ko", "en"] as const)("gives each code a plain %s sentence without the raw code", locale => {
    setLocale(locale);
    for (const code of PARSER_ERROR_CODES) {
      const sentence = parserFailureText(failure(code))!.text;
      expect(sentence.length, code).toBeGreaterThan(15);
      expect(sentence, code).not.toMatch(RAW_CODE);
      expect(sentence, code).toBe(t(`parserError.${code}` as MessageKey));
    }
  });

  it("tells the person what to do about an encoding that cannot be confirmed", () => {
    expect(parserFailureText(failure("TEXT_ENCODING_UNCONFIRMED"))).toEqual({
      code: "TEXT_ENCODING_UNCONFIRMED",
      text: "글자 인코딩을 확인하지 못했습니다. 파일을 UTF-8로 저장해 다시 올려 주세요.",
    });
    setLocale("en");
    expect(parserFailureText(failure("TEXT_ENCODING_UNCONFIRMED"))!.text).toContain("Save the file as UTF-8");
  });

  it("reads a code it has no sentence for as the general sentence and keeps the code", () => {
    const result = parserFailureText(failure("A_CODE_FROM_A_NEWER_SERVER"))!;
    expect(result.code).toBe("A_CODE_FROM_A_NEWER_SERVER");
    expect(result.text).toBe(t("parserError.unknown"));
  });

  it.each([undefined, null, "text", { details: null }, { details: {} }, { details: { parser_error: "" } }, failure(7)])(
    "returns nothing when the error carries no parser reason (%j)",
    error => expect(parserFailureText(error)).toBeNull(),
  );
});
