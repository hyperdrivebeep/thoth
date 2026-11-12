import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { LEADS, parseAnswer, splitAnswerLead } from "./answerText";
import { RESPONSE_LANGUAGE_CHOICES } from "../api/responseLanguages";

/** The partial-draft prefix of each answer language as the server writes it (request_language.py, _PARTIAL_DRAFT_PREFIX). */
function serverLeads(): Record<string, string> {
  const source = readFileSync(
    resolve(process.cwd(), "../../src/thoth/application/services/request_language.py"),
    "utf8",
  );
  const table = /_PARTIAL_DRAFT_PREFIX[^=]*=\s*\{([^}]*)\}/.exec(source);
  if (!table) throw new Error("The prefix table was not found in the server file.");
  const leads: Record<string, string> = {};
  for (const [, language, text] of table[1].matchAll(/"([^"]+)":\s*"([^"]*)"/g)) {
    leads[language] = text.replace(/\\u([0-9a-fA-F]{4})/g, (_, hex: string) =>
      String.fromCharCode(parseInt(hex, 16)),
    );
  }
  return leads;
}

it("knows the partial-draft prefix of every answer language the server writes, and no other", () => {
  const server = serverLeads();
  expect(Object.keys(server).sort()).toEqual(
    RESPONSE_LANGUAGE_CHOICES.map((choice) => choice.code).sort(),
  );
  expect([...LEADS].sort()).toEqual(Object.values(server).sort());
});

it.each(Object.entries(serverLeads()))(
  "shows a heading written right after the %s prefix as a heading",
  (language, lead) => {
    const { lead: found, body } = splitAnswerLead(lead + "### 제목 TITLE\n본문 (span:aaaa1111)");
    expect(found, language).toBe(lead.trim());
    expect(body, language).toBe("### 제목 TITLE\n본문 (span:aaaa1111)");
    const blocks = parseAnswer(body).blocks;
    expect(blocks[0], language).toMatchObject({ kind: "p", heading: 3 });
    const first = blocks[0];
    expect(
      first.kind === "p" &&
        first.inline.map((part) => (part.kind === "text" ? part.text : "")).join(""),
      language,
    ).toBe("제목 TITLE");
    expect(JSON.stringify(blocks), language).not.toContain("###");
  },
);
