import { expect, it } from "vitest";
import fixture from "../../../../tests/fixtures/language_expected_results.json";
import { expectedUnknown } from "./expectedResultState";

it("uses the shared state fixture without changing legacy Unicode or display semantics", () => {
  for (const item of fixture) {
    expect(expectedUnknown(item.row.expected ?? "", item.row.knowledge_state, "ranking"), item.name).toBe(item.web);
    expect(expectedUnknown(item.row.expected ?? "", item.row.knowledge_state, "annotation"), item.name).toBe(item.annotation);
  }
});
